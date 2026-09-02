from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "strategy.range.state"
STRATEGY_ID = "range_rotation"
EPSILON = 1e-9
DEFAULT_POSITION = 0.5
MAX_RANGE_EFFICIENCY = 0.35


@section_atom("400", "409")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._rt = StrategyRuntime(STRATEGY_ID)
        self._window = 32
        self._edge = 0.25
        self._seen = self._emitted = 0

    async def initialize(self, c):
        self._context = c
        self._rt.configure(c.config)
        self._window = int(c.config.get("tick_window", 32))
        self._edge = float(c.config.get("edge_fraction", 0.25))
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
                signal="range_unformed",
                status="insufficient_data",
            )
        else:
            low = min(prices)
            high = max(prices)
            width = high - low
            position = (prices[-1] - low) / width if width > 0 else DEFAULT_POSITION
            path = sum(abs(b - a) for a, b in zip(prices, prices[1:]))
            efficiency = abs(prices[-1] - prices[0]) / path if path else 0
            is_range = efficiency < MAX_RANGE_EFFICIENCY
            direction = (
                100.0
                if is_range and position <= self._edge
                else -100.0 if is_range and position >= 1 - self._edge else 0.0
            )
            edge_distance = min(position, 1 - position)
            strength = (
                clip((self._edge - edge_distance) / max(self._edge, EPSILON) * 100)
                if direction
                else 0
            )
            confidence = clip((1 - efficiency) * 100)
            card = self._rt.card(
                tick,
                s,
                direction=direction,
                strength=strength,
                confidence=confidence,
                signal=(
                    "lower_range_rotation"
                    if direction > 0
                    else (
                        "upper_range_rotation"
                        if direction < 0
                        else "range_middle_or_trending"
                    )
                ),
                evidence={
                    "low": low,
                    "high": high,
                    "position": position,
                    "efficiency": efficiency,
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
