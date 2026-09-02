from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import websockets

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.2.0"
PROVIDER = "MEXC"

# روابط MEXC — مؤكَّدة من الوثائق الرسمية ومختبَرة حيّاً 2026-08-26.
FUTURES_WS = "wss://contract.mexc.com/edge"       # عقود · JSON · رمز BTC_USDT
SPOT_WS = "wss://wbs-api.mexc.com/ws"             # سبوت · protobuf · رمز BTCUSDT
FUTURES_DETAIL = "https://contract.mexc.com/api/v1/contract/detail"

EVENT_TICK = "market.tick"     # ← يستهلكه مدقّق البيانات 112
EVENT_DEPTH = "market.depth"   # ← يستهلكه مستقبِل العمق 106
EVENT_TRADE = "market.trade"   # ← يستهلكه شريط الصفقات 107
EVENT_STATE = "feed.mexc.state"
EVENT_MEMBERSHIP = "crypto.universe.membership.state"   # ← كون 1001 الحيّ

_OPEN_TIMEOUT_S = 12.0
_MAX_FRAME = 2 ** 23
_MAX_BACKOFF_S = 30.0


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _levels(rows: Any, cap: int) -> list[list[float]]:
    """يحوّل [[سعر, حجم, ...], ...] إلى [[سعر, حجم], ...] مقصوصةً لأعلى المستويات."""
    out: list[list[float]] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:cap]:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            price = _f(row[0]); volume = _f(row[1])
            if price is not None and volume is not None:
                out.append([price, volume])
    return out


class Atom(AtomBase):
    """مصدر سوق MEXC عبر WebSocket — عقود (JSON) أو سبوت (protobuf).

    ينشر خامًا على الناقل: `market.tick` · `market.depth` · `market.trade`،
    فتلتقطها طبقة البيانات (112/106/107) بلا أي تعديل. مصدرٌ فقط — لا يقرأ
    الحساب ولا يرسل أمرًا. يتوسّع لأي عدد أصول: القائمة تُقسَّم آليًّا على
    اتصالات متعدّدة بحسب `max_symbols_per_conn`، وكلٌّ يتّصل ويعيد الاتصال
    بتراجع أُسّي مستقلًّا."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._market = "futures"
        self._symbols: list[str] = []
        self._depth_levels = 20
        self._batch = 40
        self._ping_s = 15.0
        self._max_age_s = 30.0
        self._sub_ticker = self._sub_depth = self._sub_deal = True
        self._tasks: list[asyncio.Task] = []
        self._conn_target = 0
        self._connected = 0
        self._counts = {"tick": 0, "depth": 0, "trade": 0}
        self._spot_frames = 0
        self._last_msg_at: float | None = None
        self._last_error = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._market = str(cfg["market_type"]).lower()
        self._symbols = [str(s).strip() for s in cfg["symbols"] if str(s).strip()]
        self._depth_levels = int(cfg.get("depth_levels", 20))
        self._batch = max(1, int(cfg.get("max_symbols_per_conn", 40)))
        self._ping_s = float(cfg.get("ping_interval_s", 15))
        self._max_age_s = float(cfg.get("max_age_s", 30))
        self._sub_ticker = bool(cfg.get("subscribe_ticker", True))
        self._sub_depth = bool(cfg.get("subscribe_depth", True))
        self._sub_deal = bool(cfg.get("subscribe_deal", True))
        context.subscribe(EVENT_MEMBERSHIP, self._on_membership)

    async def _on_membership(self, payload: dict[str, Any]) -> None:
        """يتبع كون 1001 الحيّ. الاشتراكات هنا تُثبَّت لكلّ اتصالٍ عند
        `start()` (لا تضاف/تُحذف حيًّا على اتصالٍ مفتوح) — فالتغيير الحقيقيّ
        الوحيد الصحيح هو إعادة تشغيل الطبقة كاملةً بالقائمة الجديدة، تمامًا
        كما تُقلِع أوّل مرّة. لا تُنفَّذ إلّا حين يتغيّر مجموع الرموز فعلًا —
        لا عند كل نشرة كونٍ دوريّة (كل ~poll_interval_s من 1001 بلا تغيير)."""
        if not isinstance(payload, dict):
            return
        symbols = [str(s).strip() for s in (payload.get("symbols") or []) if str(s).strip()]
        if not symbols or set(symbols) == set(self._symbols):
            return
        self._symbols = symbols
        if self._running:
            await self.stop()
            await self.start()

    async def start(self) -> None:
        if self._running or self._context is None:
            return
        self._running = True
        if self._market == "spot":
            # سبوت MEXC يبثّ market data بصيغة protobuf لا JSON — الاتصال
            # والاشتراك يعملان، لكن فكّ الحمولة يلزمه ملفّات .proto مُصرَّفة.
            self._last_error = "SPOT_PROTOBUF_DECODE_REQUIRED"
            self._context.logger.warning(
                "620 spot: MEXC spot market data is protobuf-encoded — "
                "connection/subscription work; decoding needs compiled .proto schemas")
        batches = [self._symbols[i:i + self._batch]
                   for i in range(0, len(self._symbols), self._batch)]
        self._conn_target = len(batches)
        for batch in batches:
            self._tasks.append(asyncio.create_task(self._run_conn(batch)))

    async def stop(self) -> None:
        self._running = False
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._connected = 0

    async def shutdown(self) -> None:
        await self.stop()

    # ————— دورة الاتصال بتراجع أُسّي —————
    async def _run_conn(self, symbols: list[str]) -> None:
        backoff = 1.0
        while self._running:
            try:
                await self._session(symbols)
                backoff = 1.0
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 — عزل الاتصال: خطؤه لا يسقط الذرّة
                self._last_error = "%s: %s" % (type(exc).__name__, exc)
                if self._context is not None:
                    self._context.logger.warning("620 connection error: %s", exc)
            if not self._running:
                return
            await asyncio.sleep(min(backoff, _MAX_BACKOFF_S))
            backoff *= 2

    async def _session(self, symbols: list[str]) -> None:
        url = FUTURES_WS if self._market == "futures" else SPOT_WS
        async with websockets.connect(url, ping_interval=None,
                                      open_timeout=_OPEN_TIMEOUT_S,
                                      max_size=_MAX_FRAME) as ws:
            self._connected += 1
            await self._publish_state("CONNECTED", symbols)
            ping_task: asyncio.Task | None = None
            try:
                if self._market == "futures":
                    await self._subscribe_futures(ws, symbols)
                    ping_task = asyncio.create_task(self._ping_loop(ws, {"method": "ping"}))
                    await self._recv_futures(ws)
                else:
                    await self._subscribe_spot(ws, symbols)
                    ping_task = asyncio.create_task(self._ping_loop(ws, {"method": "PING"}))
                    await self._recv_spot(ws)
            finally:
                if ping_task is not None:
                    ping_task.cancel()
                self._connected -= 1
                await self._publish_state("DISCONNECTED", symbols)

    async def _ping_loop(self, ws: Any, message: dict) -> None:
        try:
            while True:
                await asyncio.sleep(self._ping_s)
                await ws.send(json.dumps(message))
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return

    # ————— عقود (futures · JSON) —————
    async def _subscribe_futures(self, ws: Any, symbols: list[str]) -> None:
        for symbol in symbols:
            if self._sub_ticker:
                await ws.send(json.dumps({"method": "sub.ticker", "param": {"symbol": symbol}}))
            if self._sub_deal:
                await ws.send(json.dumps({"method": "sub.deal", "param": {"symbol": symbol}}))
            if self._sub_depth:
                await ws.send(json.dumps({"method": "sub.depth",
                                          "param": {"symbol": symbol, "compress": False}}))

    async def _recv_futures(self, ws: Any) -> None:
        async for raw in ws:
            if not self._running:
                return
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            await self._handle_futures(message)

    async def _handle_futures(self, message: dict) -> None:
        channel = message.get("channel", "")
        symbol = message.get("symbol", "")
        body = message.get("data")
        now = time.time()
        if channel == "push.ticker" and isinstance(body, dict):
            await self._emit_tick(symbol, body, now)
        elif channel == "push.depth" and isinstance(body, dict):
            await self._emit_depth(symbol, body, now)
        elif channel == "push.deal":
            for trade in (body if isinstance(body, list) else [body]):
                if isinstance(trade, dict):
                    await self._emit_trade(symbol, trade, now)
        # pong / rs.sub.* (تأكيدات الاشتراك) تُتجاهَل بأمان

    async def _emit_tick(self, symbol: str, body: dict, now: float) -> None:
        if self._context is None:
            return
        self._counts["tick"] += 1
        self._last_msg_at = now
        await self._context.publish(EVENT_TICK, {
            "provider": PROVIDER, "market": self._market, "symbol": symbol,
            "bid": _f(body.get("bid1")), "ask": _f(body.get("ask1")),
            "price": _f(body.get("lastPrice")), "volume": _f(body.get("volume24")),
            "timestamp": now, "source_timestamp": _f(body.get("timestamp")) or now})

    async def _emit_depth(self, symbol: str, body: dict, now: float) -> None:
        if self._context is None:
            return
        self._counts["depth"] += 1
        self._last_msg_at = now
        await self._context.publish(EVENT_DEPTH, {
            "provider": PROVIDER, "market": self._market, "symbol": symbol,
            "bids": _levels(body.get("bids"), self._depth_levels),
            "asks": _levels(body.get("asks"), self._depth_levels),
            "version": body.get("version"), "timestamp": now})

    async def _emit_trade(self, symbol: str, trade: dict, now: float) -> None:
        if self._context is None:
            return
        price = _f(trade.get("p"))
        size = _f(trade.get("v"))
        if price is None:
            return
        self._counts["trade"] += 1
        self._last_msg_at = now
        # T=1 معتدٍ شراء · T=2 معتدٍ بيع (نفس عقد شريط الصفقات).
        side = "BUY" if trade.get("T") == 1 else "SELL" if trade.get("T") == 2 else ""
        await self._context.publish(EVENT_TRADE, {
            "provider": PROVIDER, "market": self._market, "symbol": symbol,
            "price": price, "size": size, "side": side,
            "timestamp": now,
            "source_timestamp": (_f(trade.get("t")) or now * 1000) / 1000.0})

    # ————— سبوت (spot · protobuf) —————
    async def _subscribe_spot(self, ws: Any, symbols: list[str]) -> None:
        # صيغة الاشتراك الرسمية (v3 · protobuf). حدّ 30 اشتراكًا/اتصال يحكمه
        # max_symbols_per_conn في الإعداد. الرمز بلا شرطة سفلية (BTCUSDT).
        params: list[str] = []
        for symbol in symbols:
            spot_symbol = symbol.replace("_", "")
            if self._sub_deal:
                params.append("spot@public.aggre.deals.v3.api.pb@100ms@%s" % spot_symbol)
            if self._sub_depth:
                params.append("spot@public.increase.depth.v3.api.pb@%s" % spot_symbol)
        if params:
            await ws.send(json.dumps({"method": "SUBSCRIPTION", "params": params}))

    async def _recv_spot(self, ws: Any) -> None:
        # الحمولة protobuf ثنائية. نعدّ الإطارات المستلمة (دليل حياة الاتصال)
        # ولا نخترع فكًّا: الفكّ الصادق يلزمه .proto مُصرَّفة (بند للمُهدى له).
        async for _frame in ws:
            if not self._running:
                return
            self._spot_frames += 1
            self._last_msg_at = time.time()

    async def _publish_state(self, state: str, symbols: list[str]) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_STATE, {
            "provider": PROVIDER, "market": self._market, "state": state,
            "symbols": len(symbols), "connections": self._connected,
            "timestamp": time.time()})

    # ————— الصحّة تقيس استلام البيانات فعلًا، لا حياة المهمّة —————
    async def health_check(self) -> HealthStatus:
        details = {
            "market": self._market, "symbols": len(self._symbols),
            "connections": self._connected, "target_connections": self._conn_target,
            "ticks": self._counts["tick"], "depth": self._counts["depth"],
            "trades": self._counts["trade"], "spot_frames": self._spot_frames,
            "last_error": self._last_error,
            "age_s": (time.time() - self._last_msg_at) if self._last_msg_at else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._market == "spot":
            return HealthStatus(state=HealthState.DEGRADED,
                                message="SPOT_PROTOBUF_DECODE_REQUIRED", details=details)
        if self._connected <= 0:
            return HealthStatus(state=HealthState.DEGRADED, message="NO_LIVE_CONNECTION", details=details)
        age = details["age_s"]
        if age is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_MESSAGE", details=details)
        if age > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="MEXC_FEED_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="connected=%d ticks=%d depth=%d trades=%d" % (
                                self._connected, self._counts["tick"],
                                self._counts["depth"], self._counts["trade"]),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "counts": dict(self._counts),
                "spot_frames": self._spot_frames}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict) and isinstance(state.get("counts"), dict):
            for key in ("tick", "depth", "trade"):
                self._counts[key] = int(state["counts"].get(key, 0))
            self._spot_frames = int(state.get("spot_frames", 0))
