from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "strategy.session.state"
COMPONENT_ID = "session_regime"
LONDON_OPEN_UTC = 7
NEW_YORK_OPEN_UTC = 13
OVERLAP_END_UTC = 16
NEW_YORK_CLOSE_UTC = 22
PRIMARY_FACTOR = 1.0
ACTIVE_FACTOR = 0.9
OFF_PEAK_FACTOR = 0.6


@section_atom("400", "412")
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
        stamp = float(tick.get("source_timestamp") or tick.get("timestamp"))
        hour = datetime.fromtimestamp(stamp, tz=timezone.utc).hour
        if NEW_YORK_OPEN_UTC <= hour < OVERLAP_END_UTC:
            session = "overlap"
            factor = PRIMARY_FACTOR
        elif LONDON_OPEN_UTC <= hour < OVERLAP_END_UTC:
            session = "london"
            factor = ACTIVE_FACTOR
        elif NEW_YORK_OPEN_UTC <= hour < NEW_YORK_CLOSE_UTC:
            session = "new_york"
            factor = ACTIVE_FACTOR
        else:
            session = "off_peak"
            factor = OFF_PEAK_FACTOR
        card = self._rt.card(
            tick,
            s,
            direction=0,
            strength=factor * 100,
            confidence=100,
            signal="session_regime",
            context_factor=factor,
            evidence={"session": session, "utc_hour": hour, "context_factor": factor},
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
