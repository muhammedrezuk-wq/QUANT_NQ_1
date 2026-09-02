from __future__ import annotations

from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.probability_contract import TickModelRuntime, clip
from shared.section_contract import section_atom
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "probability.breakout.state"
MODEL_ID = "breakout_model"
MIN_WINDOW = 5
EPSILON = 1e-9
NEUTRAL_PROBABILITY = 0.5
MAX_PROBABILITY_EDGE = 0.49
PROBABILITY_SCALE = 204.0
DIRECTION_FULL = 100.0
CONFIDENCE_BASE = 50.0
CONFIDENCE_STRENGTH_FACTOR = 0.5


@section_atom("350", "353")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context = None
        self._running = False
        self._runtime = TickModelRuntime(MODEL_ID)
        self._window = 32
        self._buffer = 0.0
        self._ticks = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._runtime.configure(context.config)
        self._window = max(MIN_WINDOW, int(context.config.get("tick_window", 32)))
        self._buffer = (
            max(0.0, float(context.config.get("breakout_buffer_pct", 0.0))) / 100.0
        )
        context.subscribe(EVENT_TICK, self._on_tick)

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        item = self._runtime.ingest(payload)
        if item is None:
            return
        tick, state = item
        prices = list(state.prices)
        self._ticks += 1
        prior = prices[-self._window - 1 : -1]
        if len(prior) < self._window:
            card = self._runtime.card(
                tick,
                state,
                direction=0,
                strength=0,
                confidence=0,
                probability=NEUTRAL_PROBABILITY,
                signal="neutral",
                status="insufficient_data",
                evidence={"window": self._window},
            )
        else:
            price = prices[-1]
            upper = max(prior)
            lower = min(prior)
            width = max(upper - lower, price * EPSILON)
            if price > upper * (1 + self._buffer):
                direction = DIRECTION_FULL
                distance = price - upper
            elif price < lower * (1 - self._buffer):
                direction = -DIRECTION_FULL
                distance = lower - price
            else:
                direction = 0.0
                distance = 0.0
            strength = clip(distance / width * 100.0)
            confidence = clip(CONFIDENCE_BASE + strength * CONFIDENCE_STRENGTH_FACTOR)
            probability = NEUTRAL_PROBABILITY + min(
                MAX_PROBABILITY_EDGE, strength / PROBABILITY_SCALE
            )
            signal = "buy" if direction > 0 else "sell" if direction < 0 else "neutral"
            card = self._runtime.card(
                tick,
                state,
                direction=direction,
                strength=strength,
                confidence=confidence,
                probability=probability,
                signal=signal,
                evidence={"upper": upper, "lower": lower, "distance": distance},
            )
        await self._context.publish(EVENT_OUT, card)
        self._emitted += 1

    async def snapshot(self):
        return {
            "runtime": self._runtime.snapshot(),
            "ticks": self._ticks,
            "emitted": self._emitted,
        }

    async def restore(self, state):
        if isinstance(state, dict):
            self._runtime.restore(state.get("runtime"))
            self._ticks = int(state.get("ticks", 0))
            self._emitted = int(state.get("emitted", 0))

    async def health_check(self):
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY if self._ticks else HealthState.DEGRADED,
            message="ticks=%d emitted=%d" % (self._ticks, self._emitted),
            details={
                "ticks": self._ticks,
                "emitted": self._emitted,
                "invalid": self._runtime.invalid,
            },
        )
