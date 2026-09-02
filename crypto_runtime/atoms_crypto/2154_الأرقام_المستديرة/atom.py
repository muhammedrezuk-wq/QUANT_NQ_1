from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.round_numbers.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _bracket(price: float, step: float) -> dict[str, float]:
    """أقرب مستويين مستديرين (تحت/فوق) والأقرب منهما ومسافته بالنقاط."""
    below = (price // step) * step
    above = below + step
    nearest = below if (price - below) <= (above - price) else above
    return {"below": round(below, 2), "above": round(above, 2),
            "nearest": round(nearest, 2), "dist": round(abs(price - nearest), 2)}


class Atom(AtomBase):
    """الأرقام المستديرة — مستويات نفسية/أوامر.

    البشر والخوارزميّات تضع الأوامر على الأرقام الكاملة: وقفاتٌ تحت الألف،
    أهدافٌ عنده، سيولةٌ معلّقة حوله (رُصدت جدرانًا في الدفتر). ليست سحرًا بل
    تكدّس أوامر قابل للرصد. الكبرى (×الخطوة) دائمًا في الخريطة، والوسطى
    سياقًا. **شاهدٌ معزِّز لا يُتاجَر وحده** — قيمتها في التجمّع مع سبب آخر.
    الخطوات قابلة للمعايرة حسب مقياس سعر الرمز."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._max_age_s = 600.0
        self._major = 1000.0     # الكبرى — دائمًا في الخريطة
        self._mid = 500.0        # الوسطى — سياقيّة
        self._minor = 100.0      # الصغرى — لا تُتاجَر وحدها أبدًا
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeframe = str(context.config.get("timeframe", "5m"))
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        self._major = float(context.config.get("major_step", 1000.0))
        self._mid = float(context.config.get("mid_step", 500.0))
        self._minor = float(context.config.get("minor_step", 100.0))
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
        price = _f(payload.get("close"))
        if not symbol or price is None or price <= 0:
            return
        now = time.time()
        state = {"provider": payload.get("provider"), "symbol": symbol, "price": price,
                 "major": _bracket(price, self._major),
                 "mid": _bracket(price, self._mid),
                 "minor": _bracket(price, self._minor),
                 "role": "confluence_only",   # معزِّز لا يُتاجَر وحده
                 "timestamp": now}
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "steps": {"major": self._major, "mid": self._mid, "minor": self._minor}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_CANDLE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CANDLE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._state), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
