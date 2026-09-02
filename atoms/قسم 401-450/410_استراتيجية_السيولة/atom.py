from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "strategy.liquidity.state"
STRATEGY_ID = "liquidity_raid"
EPSILON = 1e-9
CONFIDENCE_BASE = 55.0
CONFIDENCE_FACTOR = 0.45


@section_atom("400", "410")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._rt = StrategyRuntime(STRATEGY_ID)
        self._window = 24
        self._seen = self._emitted = 0

    async def initialize(self, c):
        self._context = c
        self._rt.configure(c.config)
        self._window = int(c.config.get("tick_window", 24))
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
        reference = prices[-self._window - 2 : -2]
        if len(reference) < self._window:
            card = self._rt.card(
                tick,
                s,
                direction=0,
                strength=0,
                confidence=0,
                signal="liquidity_context_unformed",
                status="insufficient_data",
            )
        else:
            low = min(reference)
            high = max(reference)
            previous = prices[-2]
            current = prices[-1]
            bullish = previous < low and current >= low
            bearish = previous > high and current <= high
            direction = 100.0 if bullish else -100.0 if bearish else 0.0
            raid = low - previous if bullish else previous - high if bearish else 0
            span = max(high - low, current * EPSILON)
            strength = clip(raid / span * 100)
            confidence = clip(CONFIDENCE_BASE + strength * CONFIDENCE_FACTOR)
            card = self._rt.card(
                tick,
                s,
                direction=direction,
                strength=strength,
                confidence=confidence,
                signal=(
                    "bullish_liquidity_reclaim"
                    if bullish
                    else "bearish_liquidity_reclaim" if bearish else "no_confirmed_raid"
                ),
                evidence={
                    "reference_low": low,
                    "reference_high": high,
                    "raid_distance": raid,
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
            message="ticks=%d emitted=%d" % (self._seen, self._emitted),
            details={
                "ticks": self._seen,
                "emitted": self._emitted,
                "invalid": self._rt.invalid,
            },
        )
