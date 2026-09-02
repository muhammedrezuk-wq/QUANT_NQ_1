from __future__ import annotations

import math
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.session_levels.state"
_DAY_S = 86400.0


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """قمة/قاع الجلسة وقاع الارتداد.

    على شموع اليوم UTC: القمة والقاع بأوقاتهما، وقاع الارتداد = أدنى قاعٍ
    **بعد** شمعة القمة (خطّ إلغاء اللونغات: إغلاق ٥د تحته يُبطل الصاعد).
    يُصفَّر مع كل يوم UTC."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._max_age_s = 600.0
        self._sess: dict[str, dict[str, Any]] = {}
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
        start = _f(payload.get("period_start"))
        if not symbol or None in (high, low, start):
            return
        day = math.floor(start / _DAY_S)
        s = self._sess.get(symbol)
        if s is None or s["day"] != day:
            s = {"day": day, "high": high, "high_at": start, "low": low,
                 "low_at": start, "pullback_low": None, "pullback_at": None}
            self._sess[symbol] = s
        if high > s["high"]:
            s["high"] = high; s["high_at"] = start
            s["pullback_low"] = None; s["pullback_at"] = None   # قمة جديدة تعيد الارتداد
        if low < s["low"]:
            s["low"] = low; s["low_at"] = start
        if start > s["high_at"]:                                # بعد شمعة القمة
            if s["pullback_low"] is None or low < s["pullback_low"]:
                s["pullback_low"] = low; s["pullback_at"] = start
        now = time.time()
        state = {"provider": payload.get("provider"), "symbol": symbol,
                 "session_high": s["high"], "session_high_at": s["high_at"],
                 "session_low": s["low"], "session_low_at": s["low_at"],
                 "pullback_low": s["pullback_low"], "pullback_at": s["pullback_at"],
                 "session_start": day * _DAY_S, "timestamp": now}
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._sess), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_CANDLE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CANDLE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._sess), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
