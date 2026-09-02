from __future__ import annotations
import math
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "strategy.invalidation_quality.state"
COMPONENT_ID = "invalidation_quality"
EPSILON = 1e-9
INVALIDATION_NOISE_SCALE = 35.0


@section_atom("400", "402")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._rt = StrategyRuntime(COMPONENT_ID, directional=False)
        self._window = 20
        self._seen = self._emitted = 0

    async def initialize(self, c):
        self._context = c
        self._rt.configure(c.config)
        self._window = int(c.config.get("tick_window", 20))
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
        returns = list(s.returns)[-self._window :]
        self._seen += 1
        if len(returns) < self._window:
            pressure = 0.0
            health = 0.0
            status = "insufficient_data"
        else:
            mean = sum(returns) / len(returns)
            std = math.sqrt(sum((x - mean) ** 2 for x in returns) / len(returns))
            mean_abs = sum(abs(x) for x in returns) / len(returns)
            noise = std / max(mean_abs, EPSILON)
            pressure = clip(noise * INVALIDATION_NOISE_SCALE)
            health = clip(100 - pressure)
            status = "ok"
        card = self._rt.card(
            tick,
            s,
            direction=0,
            strength=health,
            confidence=health,
            signal="strategic_hypothesis_health",
            status=status,
            evidence={"hypothesis_health": health, "invalidation_pressure": pressure},
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
