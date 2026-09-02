from __future__ import annotations

import math
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.prior_day.state"
_DAY_S = 86400.0


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """قمة/قاع/إغلاق الأمس — PDH · PDL · PDC.

    مراجع الأمس هي أرضيّة أوامر المؤسسات (وقفات، أهداف، تقييم يوميّ). موقع
    اليوم منها حكمٌ بنيويّ فوريّ: فوق PDH نظامٌ صاعد مؤكّد، تحت PDL هابط
    مؤكّد، وبينهما يومٌ داخليّ وPDC مغناطيسه. يُبنى بتجميع شموع اليوم UTC،
    فإذا دار اليوم تجمّد يوم الأمس مرجعًا. لا تنبّؤ — مواقع معارك فقط."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._max_age_s = 600.0
        # الجاري: symbol -> {"day","high","low","close"}
        self._cur: dict[str, dict[str, float]] = {}
        # الأمس المتجمّد: symbol -> {"day","pdh","pdl","pdc"}
        self._prior: dict[str, dict[str, float]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeframe = str(context.config.get("timeframe", "5m"))
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if str(payload.get("timeframe")) != self._timeframe:
            return
        symbol = str(payload.get("symbol") or "")
        high = _f(payload.get("high")); low = _f(payload.get("low"))
        close = _f(payload.get("close")); start = _f(payload.get("period_start"))
        if not symbol or None in (high, low, close, start):
            return
        day = math.floor(start / _DAY_S)
        cur = self._cur.get(symbol)
        if cur is None:
            self._cur[symbol] = {"day": day, "high": high, "low": low, "close": close}
        elif day > cur["day"]:                                  # دار اليوم ⇒ جمّد الأمس
            self._prior[symbol] = {"day": cur["day"], "pdh": cur["high"],
                                   "pdl": cur["low"], "pdc": cur["close"]}
            self._cur[symbol] = {"day": day, "high": high, "low": low, "close": close}
        elif day == cur["day"]:                                 # داخل اليوم ⇒ راكِم
            cur["high"] = max(cur["high"], high)
            cur["low"] = min(cur["low"], low)
            cur["close"] = close
        else:                                                   # شمعة أقدم من اليوم ⇒ تُهمَل
            return
        now = time.time()
        prior = self._prior.get(symbol)
        state: dict[str, Any] = {"provider": payload.get("provider"), "symbol": symbol,
                                 "price": close, "prior_ready": prior is not None,
                                 "timestamp": now}
        if prior is not None:
            pdh = prior["pdh"]; pdl = prior["pdl"]; pdc = prior["pdc"]
            regime = "up_confirmed" if close > pdh else \
                     "down_confirmed" if close < pdl else "inside"
            state.update({"pdh": pdh, "pdl": pdl, "pdc": pdc, "regime": regime,
                          "prior_day_start": prior["day"] * _DAY_S})
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._cur), "with_prior": len(self._prior),
                   "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_CANDLE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CANDLE_STALE", details=details)
        if not self._prior:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_DAY_ROLL", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="prior=%d updates=%d" % (len(self._prior), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates, "prior": self._prior}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
            prior = state.get("prior")
            if isinstance(prior, dict):
                self._prior = prior
