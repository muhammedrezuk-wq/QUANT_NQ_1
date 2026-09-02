from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_OI = "feed.binance.oi"
EVENT_PREMIUM = "feed.binance.premium"
EVENT_OUT = "sense.binance_heat.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """حرارة Binance العالميّة — عينُنا على الكوكب (هيكليّة: بانتظار الجسر).

    تمويلُ وفروقُ عقود أكبر ساحة رافعة في العالم (~١٠٧ ألف BTC مفتوحة):
    هل ما نراه على MEXC حدثٌ محليّ أم موجةٌ عالميّة؟ بيتكوين سوقٌ واحدة
    تلحمها المراجحة، فالحدث المهمّ يظهر في الساحتين معًا — واختلافُهما نفسُه
    معلومة. سياقٌ صرف — لا يُشتقّ منه سعرُ أمرٍ أبدًا.

    **لا مصدرَ بايننس بعد**: تشترك في feed.binance.oi و feed.binance.premium
    اللذَين سيوفّرهما جسرُ بايننس مستقبلًا، وتنشر sense.binance_heat.state.
    حتى أوّل حدثٍ تبقى صحّتُها DEGRADED برسالة AWAITING_BINANCE_BRIDGE، ثم
    تتبع الدورةَ المعتادة (HEALTHY / STALE).

    تقيس عند وصول البيانات: التمويل (من حدث العلاوة) وفرقَ OI٪ على ~٣٠ دقيقة
    (سجلٌّ من حدث OI، المرجعُ أوّلُ نقطة مقابل الأخيرة كالأداة ١٠)."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_s = 1800.0
        self._flat_pct = 0.05
        self._max_age_s = 300.0
        self._max_log = 2000
        self._funding_pct: dict[str, float] = {}
        self._premium_bps: dict[str, float] = {}
        self._oi: dict[str, float] = {}
        self._oi_pct: dict[str, float | None] = {}
        self._window_min: dict[str, float | None] = {}
        self._log: dict[str, deque] = {}           # symbol → deque[(ts, oi)]
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_event_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        c = context.config
        self._window_s = float(c.get("window_s", 1800.0))
        self._flat_pct = float(c.get("oi_flat_pct", 0.05))
        self._max_age_s = float(c.get("max_age_s", 300.0))
        self._max_log = int(c.get("max_log_rows", 2000))
        context.subscribe(EVENT_OI, self._on_oi)
        context.subscribe(EVENT_PREMIUM, self._on_premium)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_oi(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        oi = _f(payload.get("oi"))
        if not symbol or oi is None or oi <= 0:
            return
        now = time.time()
        log = self._log.setdefault(symbol, deque())
        target = now - self._window_s
        ref: tuple[float, float] | None = None
        for row in log:                            # أحدثُ صفٍّ ≥ النافذة (وإلا الأقدم)
            if row[0] <= target:
                ref = row
            else:
                break
        if ref is None and log:
            ref = log[0]
        log.append((now, oi))
        while len(log) > self._max_log:
            log.popleft()
        cutoff = now - (self._window_s * 2.0 + 300.0)
        while len(log) > 2 and log[0][0] < cutoff:
            log.popleft()
        self._oi[symbol] = oi
        if ref is not None and ref[1]:
            self._oi_pct[symbol] = (oi - ref[1]) / ref[1] * 100.0
            self._window_min[symbol] = (now - ref[0]) / 60.0
        else:
            self._oi_pct[symbol] = None
            self._window_min[symbol] = None
        await self._emit(symbol, payload.get("provider"), now)

    async def _on_premium(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        pct = _f(payload.get("funding_pct"))
        if pct is None:
            rate = _f(payload.get("funding_rate"))
            if rate is None:
                rate = _f(payload.get("lastFundingRate"))
            pct = rate * 100.0 if rate is not None else None
        if pct is not None:
            self._funding_pct[symbol] = pct
        bps = _f(payload.get("premium_bps"))
        if bps is not None:
            self._premium_bps[symbol] = bps
        await self._emit(symbol, payload.get("provider"), time.time())

    async def _emit(self, symbol: str, provider: Any, now: float) -> None:
        if self._context is None:
            return
        oi_pct = self._oi_pct.get(symbol)
        if oi_pct is None:
            oi_flow = None
        elif oi_pct > self._flat_pct:
            oi_flow = "accumulating"                # تراكمٌ عالميّ
        elif oi_pct < -self._flat_pct:
            oi_flow = "unwinding"                   # تفريغٌ عالميّ
        else:
            oi_flow = "flat"
        state = {
            "provider": provider or "BINANCE", "symbol": symbol,
            "funding_pct": self._funding_pct.get(symbol),
            "premium_bps": self._premium_bps.get(symbol),
            "oi": self._oi.get(symbol),
            "oi_pct_30min": round(oi_pct, 3) if oi_pct is not None else None,
            "window_min": (round(self._window_min[symbol], 1)
                           if self._window_min.get(symbol) is not None else None),
            "oi_flow": oi_flow, "venue": "binance",
            "timestamp": now,
        }
        self._state[symbol] = state
        self._updates += 1
        self._last_event_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_event_at) if self._last_event_at else None,
                   "oi_flow": {s: v["oi_flow"] for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_event_at is None:
            # الحالة الهيكليّة: لا جسرَ بايننس بعد — لا حدثَ استُقبل قطّ.
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_BINANCE_BRIDGE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="BINANCE_HEAT_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._state), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
