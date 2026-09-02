from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "strategy.momentum.state"
STRATEGY_ID = "momentum_expansion"
RECENT_TICKS = 5
COMPARISON_TICKS = 10
MOMENTUM_SCALE = 100_000.0
ACCELERATION_SCALE = 50_000.0


@section_atom("400", "408")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._rt = StrategyRuntime(STRATEGY_ID)
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
            card = self._rt.card(
                tick,
                s,
                direction=0,
                strength=0,
                confidence=0,
                signal="momentum_unformed",
                status="insufficient_data",
            )
        else:
            net = sum(returns)
            positive = sum(x > 0 for x in returns)
            negative = sum(x < 0 for x in returns)
            persistence = max(positive, negative) / len(returns)
            recent = sum(returns[-RECENT_TICKS:])
            earlier = sum(returns[-COMPARISON_TICKS:-RECENT_TICKS])
            acceleration = abs(recent) - abs(earlier)
            direction = 100.0 if net > 0 else -100.0 if net < 0 else 0.0
            strength = clip(
                abs(net) * MOMENTUM_SCALE + max(0, acceleration) * ACCELERATION_SCALE
            )
            confidence = clip(persistence * 100)
            card = self._rt.card(
                tick,
                s,
                direction=direction,
                strength=strength,
                confidence=confidence,
                signal=(
                    "bullish_momentum_expansion"
                    if direction > 0
                    else (
                        "bearish_momentum_expansion"
                        if direction < 0
                        else "momentum_balanced"
                    )
                ),
                evidence={
                    "net_return": net,
                    "persistence": persistence,
                    "acceleration": acceleration,
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
