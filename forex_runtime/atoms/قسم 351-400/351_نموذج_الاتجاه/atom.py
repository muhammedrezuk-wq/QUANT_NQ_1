from __future__ import annotations

from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.probability_contract import TickModelRuntime, clip
from shared.section_contract import section_atom
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "probability.trend.state"
MODEL_ID = "trend_model"
NEUTRAL_PROBABILITY = 0.5
MAX_PROBABILITY_EDGE = 0.49
PROBABILITY_SCALE = 204.0
DIRECTION_FULL = 100.0
TREND_STRENGTH_SCALE = 50_000.0
CONFIDENCE_BASE = 50.0
CONFIDENCE_STRENGTH_FACTOR = 0.5


def ema(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    value = values[0]
    for item in values[1:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


@section_atom("350", "351")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._runtime = TickModelRuntime(MODEL_ID)
        self._fast = 20
        self._slow = 50
        self._ticks = self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._fast = int(context.config.get("ema_fast", 20))
        self._slow = int(context.config.get("ema_slow", 50))
        self._runtime.configure(context.config)
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
        ingested = self._runtime.ingest(payload)
        if ingested is None:
            return
        tick, state = ingested
        prices = list(state.prices)
        self._ticks += 1
        if len(prices) < self._slow:
            card = self._runtime.card(
                tick,
                state,
                direction=0,
                strength=0,
                confidence=0,
                probability=NEUTRAL_PROBABILITY,
                signal="neutral",
                status="insufficient_data",
                evidence={"fast": self._fast, "slow": self._slow},
            )
        else:
            fast = ema(prices[-self._slow :], self._fast)
            slow = ema(prices[-self._slow :], self._slow)
            distance = (fast - slow) / slow if slow else 0.0
            direction = (
                DIRECTION_FULL
                if distance > 0
                else -DIRECTION_FULL if distance < 0 else 0.0
            )
            strength = clip(abs(distance) * TREND_STRENGTH_SCALE)
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
                evidence={"ema_fast": fast, "ema_slow": slow, "distance": distance},
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
