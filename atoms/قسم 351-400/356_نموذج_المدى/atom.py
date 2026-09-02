from __future__ import annotations

from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.probability_contract import TickModelRuntime, clip
from shared.section_contract import section_atom
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "probability.range.state"
MODEL_ID = "range_model"
MIN_WINDOW = 6
NEUTRAL_PROBABILITY = 0.5
PERCENT = 100.0


@section_atom("350", "356")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._runtime = TickModelRuntime(MODEL_ID)
        self._window = 32
        self._max_efficiency = 0.35
        self._ticks = 0
        self._emitted = 0

    async def initialize(self, context):
        self._context = context
        self._runtime.configure(context.config)
        self._window = max(MIN_WINDOW, int(context.config.get("tick_window", 32)))
        self._max_efficiency = float(context.config.get("max_range_efficiency", 0.35))
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
                directional=False,
                evidence={"window": self._window},
            )
        else:
            path = sum(abs(b - a) for a, b in zip(prices, prices[1:]))
            net = abs(prices[-1] - prices[0])
            efficiency = net / path if path > 0 else 0.0
            range_probability = max(0.0, min(1.0, 1.0 - efficiency))
            strength = clip(range_probability * PERCENT)
            confidence = clip(len(prices) / self._window * PERCENT)
            signal = "ranging" if efficiency <= self._max_efficiency else "trending"
            card = self._runtime.card(
                tick,
                state,
                direction=0,
                strength=strength,
                confidence=confidence,
                probability=range_probability,
                signal=signal,
                directional=False,
                evidence={"efficiency": efficiency, "path": path, "net": net},
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
