from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.volume_ma.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """حجم الشمعة مقابل متوسّطه MA20 — أبِجيشٍ الحركةُ أم بدوريّة؟

    الحجم صوتُ المشاركة الحقيقية: كسرٌ بحجمٍ مضاعف التزامٌ جماعيّ، وكسرٌ بحجمٍ
    ذابلٍ غالبًا خدعة. النسبة = حجم الشمعة الجارية ÷ متوسّط آخر 20 شمعة
    (الجارية مستثناةٌ من متوسّطها). تصنيفٌ ثلاثيّ من عتبات الورقة: تصاعدٌ
    (surge ≥×2)، ذروةٌ (climax ≥×2.5)، ذبولٌ (fade <×0.7)."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._ma_length = 20
        self._breakout_mult = 2.0
        self._climax_mult = 2.5
        self._fade_mult = 0.7
        self._max_age_s = 600.0
        self._vols: dict[str, deque] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeframe = str(context.config.get("timeframe", "5m"))
        self._ma_length = int(context.config.get("ma_length", 20))
        self._breakout_mult = float(context.config.get("breakout_mult", 2.0))
        self._climax_mult = float(context.config.get("climax_mult", 2.5))
        self._fade_mult = float(context.config.get("fade_mult", 0.7))
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _classify(self, ratio: float | None) -> str:
        if ratio is None:
            return "warming"
        if ratio >= self._climax_mult:
            return "climax"
        if ratio >= self._breakout_mult:
            return "surge"
        if ratio < self._fade_mult:
            return "fade"
        return "normal"

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if str(payload.get("timeframe")) != self._timeframe:
            return
        symbol = str(payload.get("symbol") or "")
        close = _f(payload.get("close")); volume = _f(payload.get("volume"))
        if not symbol or None in (close, volume) or volume < 0:
            return
        window = self._vols.get(symbol)
        if window is None:
            window = deque(maxlen=self._ma_length)
            self._vols[symbol] = window
        # المتوسط من الشموع **السابقة** فقط — الجارية مستثناة، ثم تُضاف بعده.
        ma = sum(window) / self._ma_length if len(window) == self._ma_length else None
        ratio = (volume / ma) if (ma is not None and ma > 0) else None
        window.append(volume)
        signal = self._classify(ratio)
        now = time.time()
        state = {"provider": payload.get("provider"), "symbol": symbol,
                 "volume": volume, "ma": round(ma, 8) if ma is not None else None,
                 "ratio": round(ratio, 4) if ratio is not None else None,
                 "signal": signal, "price": close, "bars": len(window),
                 "ma_length": self._ma_length, "timestamp": now}
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "ratio": {s: v["ratio"] for s, v in self._state.items()}}
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
