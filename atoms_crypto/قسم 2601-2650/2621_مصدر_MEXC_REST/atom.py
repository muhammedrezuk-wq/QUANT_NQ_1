from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.3.0"
PROVIDER = "MEXC"
BASE = "https://contract.mexc.com/api/v1/contract"

EVENT_CANDLE = "market.candle"      # ← الحواسّ الكلاسيكية (VWAP/مستويات/مدى…)
EVENT_OI = "market.oi"              # ← التموضع (9) والوقود (10)
EVENT_FUNDING = "market.funding"    # ← التمويل (11)
EVENT_PREMIUM = "market.premium"    # ← العلاوة (12)
EVENT_DEPTH = "market.depth"        # ← الجدران (14) — سنابشوت كامل، لا دلتا WS
EVENT_STATE = "feed.mexc_rest.state"
EVENT_MEMBERSHIP = "crypto.universe.membership.state"   # ← كون 1001 الحيّ

# أسماء MEXC للأطر ⟵ ثوانيها وتسميتها المختصرة على الناقل.
_FRAME_SECONDS = {"Min1": 60, "Min5": 300, "Min15": 900, "Min30": 1800,
                  "Min60": 3600, "Hour4": 14400, "Hour8": 28800, "Day1": 86400}
_FRAME_LABEL = {"Min1": "1m", "Min5": "5m", "Min15": "15m", "Min30": "30m",
                "Min60": "1h", "Hour4": "4h", "Hour8": "8h", "Day1": "1d"}
_HTTP_TIMEOUT = 12.0


def _get(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "asmar/1.0"})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _rows(raw: Any, cap: int) -> list[list[float]]:
    """[[سعر, حجم, عدد], ...] الخام ⇒ [[سعر, حجم], ...] مقصوصةً لأعلى المستويات."""
    out: list[list[float]] = []
    if not isinstance(raw, list):
        return out
    for row in raw[:cap]:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            price, size = _f(row[0]), _f(row[1])
            if price is not None and size is not None and price > 0 and size >= 0:
                out.append([price, size])
    return out


class Atom(AtomBase):
    """مصدر MEXC REST — الشموع المغلقة الحقيقية والتموضع.

    يستطلع دوريًّا لكل رمز: klines (كل إطار) وticker (holdVol=OI · fair/index=
    علاوة) وfunding ودفتر الأوامر الكامل (bids/asks). ينشر **الشموع المغلقة
    الجديدة فقط** (لا يعيد نشر ما نشره)، والتموضع مع كل استطلاع. قراءة فقط —
    لا حساب ولا أمر. يكمّل 620 اللحظيّ.

    العمق تحديدًا **لا يُؤخذ من WS 620**: push.depth هناك دلتا جهةٍ واحدة في
    كل رسالة (مقاس حيًّا: صفر من 40 رسالة حملت الجهتين معًا) — لا يصلح
    مصدرًا لسنابشوتٍ كامل. المنهجية المُثبَتة (mexc_read.py) تقرأ
    `/contract/depth/{symbol}?limit=100` كسنابشوتٍ كاملٍ دوريًّا؛ هذا ما
    تكرّره `_poll_depth` هنا حرفيًّا."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._symbols: list[str] = []
        self._frames: list[str] = []
        self._kline_poll_s = 10.0
        self._ticker_poll_s = 5.0
        self._depth_poll_s = 5.0
        self._depth_limit = 100
        self._max_age_s = 60.0
        self._warmup_bars = 200
        self._task_kline: asyncio.Task | None = None
        self._task_ticker: asyncio.Task | None = None
        self._task_depth: asyncio.Task | None = None
        self._last_closed: dict[tuple[str, str], float] = {}   # (symbol, frame) → period_start
        self._candles = 0
        self._ticker_polls = 0
        self._depth_polls = 0
        self._last_ok: float | None = None
        self._last_error = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._symbols = [str(s).strip() for s in cfg["symbols"] if str(s).strip()]
        self._frames = [str(f).strip() for f in cfg["timeframes"]
                        if str(f).strip() in _FRAME_SECONDS]
        self._kline_poll_s = float(cfg.get("kline_poll_s", 10.0))
        self._ticker_poll_s = float(cfg.get("ticker_poll_s", 5.0))
        self._depth_poll_s = float(cfg.get("depth_poll_s", 5.0))
        self._depth_limit = int(cfg.get("depth_limit", 100))
        self._max_age_s = float(cfg.get("max_age_s", 60.0))
        self._warmup_bars = int(cfg.get("warmup_bars", 200))
        context.subscribe(EVENT_MEMBERSHIP, self._on_membership)

    async def _on_membership(self, payload: dict[str, Any]) -> None:
        """يتبع كون 1001 الحيّ — لا قائمةً مجمَّدة. رموزٌ تُقصى من الكون
        تتوقّف عن الاستطلاع تلقائيًّا في الدورة التالية (بلا حاجة لإعادة
        تشغيل: كل حلقة استطلاع تقرأ self._symbols حديثًا)."""
        if not isinstance(payload, dict):
            return
        symbols = [str(s).strip() for s in (payload.get("symbols") or []) if str(s).strip()]
        if symbols and set(symbols) != set(self._symbols):
            self._symbols = symbols

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task_kline = asyncio.create_task(self._loop(self._poll_klines, self._kline_poll_s))
        self._task_ticker = asyncio.create_task(self._loop(self._poll_ticker, self._ticker_poll_s))
        self._task_depth = asyncio.create_task(self._loop(self._poll_depth, self._depth_poll_s))

    async def stop(self) -> None:
        self._running = False
        for task in (self._task_kline, self._task_ticker, self._task_depth):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task_kline = self._task_ticker = self._task_depth = None

    async def shutdown(self) -> None:
        await self.stop()

    async def _loop(self, poll, interval: float) -> None:
        try:
            while self._running:
                try:
                    await poll()
                except Exception as exc:  # noqa: BLE001 — عزل: خطأ استطلاع لا يسقط الذرّة
                    self._last_error = "%s: %s" % (type(exc).__name__, exc)
                    if self._context is not None:
                        self._context.logger.warning("621 poll error: %s", exc)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    # ————— الشموع —————
    async def _poll_klines(self) -> None:
        if self._context is None:
            return
        for symbol in self._symbols:
            for frame in self._frames:
                data = await asyncio.to_thread(
                    _get, "%s/kline/%s?interval=%s" % (BASE, symbol, frame))
                await self._emit_closed(symbol, frame, data)

    async def _emit_closed(self, symbol: str, frame: str, data: Any) -> None:
        rows = (data or {}).get("data") or {}
        times = rows.get("time") or []
        if not times or self._context is None:
            return
        seconds = _FRAME_SECONDS[frame]
        now = time.time()
        opens, highs = rows.get("open") or [], rows.get("high") or []
        lows, closes = rows.get("low") or [], rows.get("close") or []
        vols = rows.get("vol") or rows.get("realVol") or []
        key = (symbol, frame)
        if key not in self._last_closed:
            # الإحماء: أوّل استطلاع ينشر آخر warmup_bars مغلقةً فقط، لا كلّ
            # التاريخ (2000). نضع الأرضية عند الشمعة السابقة لنافذة الإحماء.
            closed = [t for t in (_f(x) for x in times)
                      if t is not None and now >= t + seconds]
            self._last_closed[key] = (closed[-self._warmup_bars - 1]
                                      if len(closed) > self._warmup_bars else 0.0)
        last_seen = self._last_closed[key]
        # آخر شمعة قد تكون جارية (غير مغلقة) — تُغلَق حين يمرّ زمن إطارها.
        for i, start in enumerate(times):
            start = _f(start)
            if start is None or start <= last_seen:
                continue
            if now < start + seconds:        # لم يكتمل الإطار بعد ⇒ شمعة جارية
                continue
            if i >= len(closes):
                continue
            self._last_closed[key] = start
            self._candles += 1
            self._last_ok = now
            await self._context.publish(EVENT_CANDLE, {
                "provider": PROVIDER, "symbol": symbol, "timeframe": _FRAME_LABEL[frame],
                "open": _f(opens[i]) if i < len(opens) else None,
                "high": _f(highs[i]) if i < len(highs) else None,
                "low": _f(lows[i]) if i < len(lows) else None,
                "close": _f(closes[i]),
                "volume": _f(vols[i]) if i < len(vols) else None,
                "period_start": start, "closed": True, "timestamp": now})

    # ————— التموضع —————
    async def _poll_ticker(self) -> None:
        if self._context is None:
            return
        for symbol in self._symbols:
            data = (await asyncio.to_thread(_get, "%s/ticker?symbol=%s" % (BASE, symbol))).get("data") or {}
            funding = (await asyncio.to_thread(_get, "%s/funding_rate/%s" % (BASE, symbol))).get("data") or {}
            now = time.time()
            oi = _f(data.get("holdVol"))
            fair = _f(data.get("fairPrice"))
            index = _f(data.get("indexPrice"))
            rate = _f(funding.get("fundingRate"))
            self._ticker_polls += 1
            self._last_ok = now
            if oi is not None:
                await self._context.publish(EVENT_OI, {
                    "provider": PROVIDER, "symbol": symbol, "oi": oi, "timestamp": now})
            if rate is not None:
                await self._context.publish(EVENT_FUNDING, {
                    "provider": PROVIDER, "symbol": symbol, "funding_rate": rate,
                    "funding_pct": rate * 100.0, "timestamp": now})
            if fair is not None and index is not None and index > 0:
                await self._context.publish(EVENT_PREMIUM, {
                    "provider": PROVIDER, "symbol": symbol,
                    "premium_bps": round((fair - index) / index * 1e4, 3),
                    "fair_price": fair, "index_price": index, "timestamp": now})

    # ————— دفتر الأوامر (سنابشوت كامل — ليس دلتا WS) —————
    async def _poll_depth(self) -> None:
        if self._context is None:
            return
        for symbol in self._symbols:
            data = (await asyncio.to_thread(
                _get, "%s/depth/%s?limit=%d" % (BASE, symbol, self._depth_limit))).get("data") or {}
            bids = _rows(data.get("bids"), self._depth_limit)
            asks = _rows(data.get("asks"), self._depth_limit)
            now = time.time()
            self._depth_polls += 1
            self._last_ok = now
            if not bids or not asks:
                continue
            await self._context.publish(EVENT_DEPTH, {
                "provider": PROVIDER, "symbol": symbol,
                "bids": bids, "asks": asks, "timestamp": now})

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._symbols), "frames": self._frames,
                   "candles": self._candles, "ticker_polls": self._ticker_polls,
                   "depth_polls": self._depth_polls,
                   "last_error": self._last_error,
                   "age_s": (time.time() - self._last_ok) if self._last_ok else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_ok is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_POLL", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="MEXC_REST_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="candles=%d ticker=%d depth=%d" % (
                                self._candles, self._ticker_polls, self._depth_polls),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "candles": self._candles}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._candles = int(state.get("candles", 0))
