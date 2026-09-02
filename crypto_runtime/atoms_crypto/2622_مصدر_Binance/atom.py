from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
PROVIDER = "BINANCE"
BASE = "https://fapi.binance.com/fapi/v1"

EVENT_OI = "feed.binance.oi"            # ← حرارة بايننس (174)
EVENT_PREMIUM = "feed.binance.premium"  # ← حرارة بايننس (174)
EVENT_STATE = "feed.binance_rest.state"
EVENT_MEMBERSHIP = "crypto.universe.membership.state"   # ← كون 1001 الحيّ

_HTTP_TIMEOUT = 10.0


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


class Atom(AtomBase):
    """جسر بايننس العالمي — عينُنا على الكوكب لحرارة بايننس (174).

    يستطلع دوريًّا لكل رمز: `premiumIndex` (lastFundingRate + markPrice/
    indexPrice ⇒ premium_bps بنفس صيغة 2621 لـMEXC) و`openInterest` (قراءةٌ
    خامّةٌ لحظية — 174 نفسها تحسب فرق الـ30 دقيقة من سجلّها الداخلي، تمامًا
    كما تفعل 2171 لـMEXC؛ لا تكرار حسابٍ هنا).

    فشلُ رمزٍ واحد (غير مُدرَج على بايننس، أو حظرٌ جغرافي) **لا يُسقط
    البقية** — تصميمٌ مقصودٌ يطابق مبدأ الورقة الأصل حرفيًّا: "فشل/حظر
    جغرافي ⇒ يطبع unavailable ولا يُسقط القراءة". قراءة فقط — لا حساب
    اتجاهٍ ولا أمر. رموزٌ بلا مطابقةٍ مؤكَّدة على بايننس (كـPUMPFUN) تُترَك
    خارج القائمة عمدًا بدل تخمين رمزٍ قد يكون أصلًا مختلفًا كليًّا."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._symbols: list[str] = []
        self._symbol_map: dict[str, str] = {}
        self._poll_s = 10.0
        self._max_age_s = 60.0
        self._task: asyncio.Task | None = None
        self._ok = {"oi": 0, "premium": 0}
        self._failed: dict[str, str] = {}   # رمز MEXC → آخر خطأ (يُمسَح عند النجاح)
        self._last_ok: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._symbols = [str(s).strip() for s in cfg["symbols"] if str(s).strip()]
        self._symbol_map = {str(k): str(v) for k, v in (cfg.get("symbol_map") or {}).items()}
        self._poll_s = float(cfg.get("poll_s", 10.0))
        self._max_age_s = float(cfg.get("max_age_s", 60.0))
        context.subscribe(EVENT_MEMBERSHIP, self._on_membership)

    async def _on_membership(self, payload: dict[str, Any]) -> None:
        """يتبع كون 1001 الحيّ — لا قائمةً مجمَّدة. رمزٌ جديد بلا مطابقةٍ في
        symbol_map يُحاوَل بتحويل افتراضيّ (حذف الشرطة السفلية)؛ إن لم يوجد
        على بايننس يُعزَل فشله في _failed كأي رمزٍ آخر (بلا إسقاط الذرّة)."""
        if not isinstance(payload, dict):
            return
        symbols = [str(s).strip() for s in (payload.get("symbols") or []) if str(s).strip()]
        if symbols and set(symbols) != set(self._symbols):
            self._symbols = symbols

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def shutdown(self) -> None:
        await self.stop()

    def _binance_symbol(self, symbol: str) -> str:
        return self._symbol_map.get(symbol) or symbol.replace("_", "")

    async def _loop(self) -> None:
        try:
            while self._running:
                for symbol in self._symbols:
                    await self._poll_premium(symbol)
                    await self._poll_oi(symbol)
                await asyncio.sleep(self._poll_s)
        except asyncio.CancelledError:
            return

    async def _poll_premium(self, symbol: str) -> None:
        if self._context is None:
            return
        bsym = self._binance_symbol(symbol)
        try:
            data = await asyncio.to_thread(_get, "%s/premiumIndex?symbol=%s" % (BASE, bsym))
        except Exception as exc:  # noqa: BLE001 — عزل: رمزٌ واحد فاشل لا يُسقط الباقين
            self._failed[symbol] = "premiumIndex %s: %s" % (type(exc).__name__, exc)
            return
        rate = _f(data.get("lastFundingRate"))
        mark = _f(data.get("markPrice"))
        index = _f(data.get("indexPrice"))
        now = time.time()
        payload: dict[str, Any] = {"provider": PROVIDER, "symbol": symbol, "timestamp": now}
        if rate is not None:
            payload["funding_pct"] = round(rate * 100.0, 6)
        if mark is not None and index is not None and index > 0:
            payload["premium_bps"] = round((mark - index) / index * 1e4, 3)
        if "funding_pct" not in payload and "premium_bps" not in payload:
            self._failed[symbol] = "premiumIndex: missing fields"
            return
        self._failed.pop(symbol, None)
        self._ok["premium"] += 1
        self._last_ok = now
        await self._context.publish(EVENT_PREMIUM, payload)

    async def _poll_oi(self, symbol: str) -> None:
        if self._context is None:
            return
        bsym = self._binance_symbol(symbol)
        try:
            data = await asyncio.to_thread(_get, "%s/openInterest?symbol=%s" % (BASE, bsym))
        except Exception as exc:  # noqa: BLE001
            self._failed[symbol] = "openInterest %s: %s" % (type(exc).__name__, exc)
            return
        oi = _f(data.get("openInterest"))
        if oi is None or oi <= 0:
            self._failed[symbol] = "openInterest: missing/invalid"
            return
        self._failed.pop(symbol, None)
        now = time.time()
        self._ok["oi"] += 1
        self._last_ok = now
        await self._context.publish(EVENT_OI, {
            "provider": PROVIDER, "symbol": symbol, "oi": oi, "timestamp": now})

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._symbols), "oi_ok": self._ok["oi"],
                   "premium_ok": self._ok["premium"], "failed": dict(self._failed),
                   "age_s": (time.time() - self._last_ok) if self._last_ok else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_ok is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_POLL", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="BINANCE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="oi=%d premium=%d failed=%d" % (
                                self._ok["oi"], self._ok["premium"], len(self._failed)),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "ok": dict(self._ok)}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict) and isinstance(state.get("ok"), dict):
            for key in ("oi", "premium"):
                self._ok[key] = int(state["ok"].get(key, 0))
