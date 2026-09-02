from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "strategy.entry_quality.state"
COMPONENT_ID = "entry_structure_quality"
SPREAD_BLEND = 0.55
MOVEMENT_BLEND = 0.45


@section_atom("400", "401")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._rt = StrategyRuntime(COMPONENT_ID, directional=False)
        self._seen = self._emitted = 0

    async def initialize(self, c):
        self._context = c
        self._rt.configure(c.config)
        c.subscribe(EVENT_TICK, self._on_tick)

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    async def _on_tick(self, p: dict[str, Any]):
        if not self._running or self._context is None or not isinstance(p, dict):
            return
        item = self._rt.ingest(p)
        if item is None:
            return
        tick, s = item
        self._seen += 1
        depth, parts = self._rt.depth(s)
        spread_quality = parts["spread_quality"]
        movement = parts["movement"]
        quality = clip(SPREAD_BLEND * spread_quality + MOVEMENT_BLEND * movement)
        confidence = clip(depth)
        card = self._rt.card(
            tick,
            s,
            direction=0,
            strength=quality,
            confidence=confidence,
            signal="strategic_trigger_quality",
            evidence={
                "entry_structure_quality": quality,
                "trigger_quality": movement,
                "spread_quality": spread_quality,
            },
        )
        await self._context.publish(EVENT_OUT, card)
        self._emitted += 1

    async def snapshot(self):
        return {
            "runtime": self._rt.snapshot(),
            "seen": self._seen,
            "emitted": self._emitted,
        }

    async def restore(self, x):
        if isinstance(x, dict):
            self._rt.restore(x.get("runtime"))
            self._seen = int(x.get("seen", 0))
            self._emitted = int(x.get("emitted", 0))

    async def health_check(self):
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY if self._seen else HealthState.DEGRADED,
            message="ticks=%d" % self._seen,
            details={
                "ticks": self._seen,
                "emitted": self._emitted,
                "invalid": self._rt.invalid,
            },
        )
