from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "strategy.trend.state"
STRATEGY_ID = "trend_continuation"
TREND_STRENGTH_SCALE = 50_000.0
CONFIDENCE_BASE = 50.0
CONFIDENCE_FACTOR = 0.5


@section_atom("400", "404")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._rt = StrategyRuntime(STRATEGY_ID)
        self._fast = 12
        self._slow = 36
        self._seen = self._emitted = 0

    async def initialize(self, c):
        self._context = c
        self._rt.configure(c.config)
        self._fast = int(c.config.get("fast_ticks", 12))
        self._slow = int(c.config.get("slow_ticks", 36))
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
        prices = list(s.prices)
        self._seen += 1
        if len(prices) < self._slow:
            card = self._rt.card(
                tick,
                s,
                direction=0,
                strength=0,
                confidence=0,
                signal="trend_unformed",
                status="insufficient_data",
            )
        else:
            fast = sum(prices[-self._fast :]) / self._fast
            slow = sum(prices[-self._slow :]) / self._slow
            delta = fast / slow - 1 if slow else 0
            direction = 100.0 if delta > 0 else -100.0 if delta < 0 else 0.0
            strength = clip(abs(delta) * TREND_STRENGTH_SCALE)
            confidence = clip(CONFIDENCE_BASE + strength * CONFIDENCE_FACTOR)
            card = self._rt.card(
                tick,
                s,
                direction=direction,
                strength=strength,
                confidence=confidence,
                signal=(
                    "bullish_continuation"
                    if direction > 0
                    else "bearish_continuation" if direction < 0 else "balanced"
                ),
                evidence={"fast_mean": fast, "slow_mean": slow, "distance": delta},
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
            message="ticks=%d emitted=%d" % (self._seen, self._emitted),
            details={
                "ticks": self._seen,
                "emitted": self._emitted,
                "invalid": self._rt.invalid,
            },
        )
