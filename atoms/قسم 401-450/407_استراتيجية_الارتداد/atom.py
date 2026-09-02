from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "strategy.pullback.state"
STRATEGY_ID = "pullback_quality"
PULLBACK_STRENGTH_SCALE = 100_000.0
CONFIDENCE_BASE = 45.0
CONFIDENCE_FACTOR = 0.55


@section_atom("400", "407")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._rt = StrategyRuntime(STRATEGY_ID)
        self._window = 32
        self._minimum = 0.0001
        self._seen = self._emitted = 0

    async def initialize(self, c):
        self._context = c
        self._rt.configure(c.config)
        self._window = int(c.config.get("tick_window", 32))
        self._minimum = float(c.config.get("pullback_min_pct", 0.01)) / 100
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
        prices = list(s.prices)[-self._window :]
        self._seen += 1
        if len(prices) < self._window:
            card = self._rt.card(
                tick,
                s,
                direction=0,
                strength=0,
                confidence=0,
                signal="pullback_unformed",
                status="insufficient_data",
            )
        else:
            half = len(prices) // 2
            early = sum(prices[:half]) / half
            late = sum(prices[half:]) / (len(prices) - half)
            trend = late / early - 1 if early else 0
            mean = sum(prices) / len(prices)
            deviation = prices[-1] / mean - 1 if mean else 0
            qualified = (trend > 0 and deviation <= -self._minimum) or (
                trend < 0 and deviation >= self._minimum
            )
            direction = (
                100.0
                if qualified and trend > 0
                else -100.0 if qualified and trend < 0 else 0.0
            )
            strength = clip(abs(deviation) * PULLBACK_STRENGTH_SCALE)
            confidence = clip(CONFIDENCE_BASE + strength * CONFIDENCE_FACTOR)
            card = self._rt.card(
                tick,
                s,
                direction=direction,
                strength=strength,
                confidence=confidence,
                signal=(
                    "bullish_pullback_quality"
                    if direction > 0
                    else (
                        "bearish_pullback_quality"
                        if direction < 0
                        else "pullback_not_qualified"
                    )
                ),
                evidence={"trend": trend, "deviation": deviation, "mean": mean},
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
