from __future__ import annotations

import math
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.probability_contract import TickModelRuntime, clip
from shared.section_contract import section_atom
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "probability.reversal.state"
MODEL_ID = "reversal_model"
MIN_WINDOW = 5
EPSILON = 1e-9
NEUTRAL_PROBABILITY = 0.5
MAX_PROBABILITY_EDGE = 0.49
PROBABILITY_SCALE = 204.0
DIRECTION_FULL = 100.0
STRENGTH_AT_THRESHOLD = 60.0
CONFIDENCE_BASE = 45.0
CONFIDENCE_STRENGTH_FACTOR = 0.55


@section_atom("350", "352")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._runtime = TickModelRuntime(MODEL_ID)
        self._window = 32
        self._z = 1.5
        self._ticks = self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._runtime.configure(context.config)
        self._window = max(MIN_WINDOW, int(context.config.get("tick_window", 32)))
        self._z = float(context.config.get("z_threshold", 1.5))
        context.subscribe(EVENT_TICK, self._on_tick)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        item = self._runtime.ingest(payload)
        if item is None:
            return
        tick, state = item
        prices = list(state.prices)[-self._window :]
        self._ticks += 1
        if len(prices) < self._window:
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
            mean = sum(prices) / len(prices)
            variance = sum((x - mean) ** 2 for x in prices) / len(prices)
            deviation = math.sqrt(variance)
            z = (prices[-1] - mean) / deviation if deviation > 0 else 0.0
            direction = (
                -DIRECTION_FULL
                if z >= self._z
                else DIRECTION_FULL if z <= -self._z else 0.0
            )
            strength = clip(abs(z) / max(self._z, EPSILON) * STRENGTH_AT_THRESHOLD)
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
                evidence={"z_score": z, "mean": mean, "std": deviation},
            )
        await self._context.publish(EVENT_OUT, card)
        self._emitted += 1

    async def snapshot(self) -> dict[str, Any]:
        return {
            "runtime": self._runtime.snapshot(),
            "ticks": self._ticks,
            "emitted": self._emitted,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._runtime.restore(state.get("runtime"))
            self._ticks = int(state.get("ticks", 0))
            self._emitted = int(state.get("emitted", 0))

    async def health_check(self) -> HealthStatus:
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
