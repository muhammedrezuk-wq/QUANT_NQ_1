from __future__ import annotations

from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.probability_contract import TickModelRuntime, clip
from shared.section_contract import section_atom
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "probability.momentum.state"
MODEL_ID = "momentum_model"
MIN_WINDOW = 5
NEUTRAL_PROBABILITY = 0.5
MAX_PROBABILITY_EDGE = 0.49
PROBABILITY_SCALE = 204.0
DIRECTION_FULL = 100.0
MOMENTUM_STRENGTH_SCALE = 100_000.0


@section_atom("350", "355")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._runtime = TickModelRuntime(MODEL_ID)
        self._window = 20
        self._ticks = 0
        self._emitted = 0

    async def initialize(self, context):
        self._context = context
        self._runtime.configure(context.config)
        self._window = max(MIN_WINDOW, int(context.config.get("tick_window", 20)))
        context.subscribe(EVENT_TICK, self._on_tick)

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    async def _on_tick(self, payload: dict[str, Any]):
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        item = self._runtime.ingest(payload)
        if item is None:
            return
        tick, state = item
        returns = list(state.returns)[-self._window :]
        self._ticks += 1
        if len(returns) < self._window:
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
            net = sum(returns)
            positive = sum(1 for value in returns if value > 0)
            negative = sum(1 for value in returns if value < 0)
            persistence = max(positive, negative) / len(returns)
            direction = (
                DIRECTION_FULL if net > 0 else -DIRECTION_FULL if net < 0 else 0.0
            )
            strength = clip(abs(net) * MOMENTUM_STRENGTH_SCALE)
            confidence = clip(persistence * DIRECTION_FULL)
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
                evidence={
                    "net_return": net,
                    "persistence": persistence,
                    "positive": positive,
                    "negative": negative,
                },
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
