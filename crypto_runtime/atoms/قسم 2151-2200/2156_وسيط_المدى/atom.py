from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.median_range.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _median(values: list[float]) -> float:
    """الوسيط بعُرف المصدر: العنصر الأوسط الأعلى للقائمة المرتّبة (rng[len//2])
    — لا يُتوسَّط الوسطان، مطابقةً لِـ mexc_read كي يتّحد مع علم الشذوذ (158)."""
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


class Atom(AtomBase):
    """وسيط مدى الشمعة — نبض التقلّب ومُدخل البوابة الاقتصادية.

    المدى = (H−L) لكل شمعة؛ ووسيطُ آخر 12 شمعة يُعطي التقلّب اللحظيّ النموذجيّ
    (الوقود الذي تُدفع منه الكلفة). اختيار **الوسيط** لا المتوسط مقصود: شمعة
    شلال واحدة تُفسد المتوسط أيامًا ولا تُحرّك الوسيط. يُصدَّر بالنقاط وبالنقطة
    الأساس (÷السعر ×10⁴). لا حركة كافية ⇒ لا صفقة مهما جمُل الشكل."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._window = 12
        self._max_age_s = 600.0
        self._ranges: dict[str, deque] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeframe = str(context.config.get("timeframe", "5m"))
        self._window = int(context.config.get("window", 12))
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
        close = _f(payload.get("close"))
        if not symbol or None in (high, low, close) or high < low or close <= 0:
            return
        window = self._ranges.get(symbol)
        if window is None:
            window = deque(maxlen=self._window)
            self._ranges[symbol] = window
        window.append(high - low)
        med = _median(list(window))
        now = time.time()
        state = {"provider": payload.get("provider"), "symbol": symbol,
                 "median_range": round(med, 8),
                 "median_bps": round(med / close * 1e4, 3),
                 "price": close, "bars": len(window),
                 "window": self._window, "timestamp": now}
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "median_bps": {s: v["median_bps"] for s, v in self._state.items()}}
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
