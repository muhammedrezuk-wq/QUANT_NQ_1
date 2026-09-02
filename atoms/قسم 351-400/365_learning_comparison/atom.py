from __future__ import annotations

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.0.4"

EVENT_VALIDATED = "learning.model.validated"
EVENT_ACTIVE = "learning.model.active.state"
EVENT_OUT = "learning.model.selection"


@section_atom("350", "365")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._active: dict | None = None
        self._seen = 0
        self._improvement = 0.0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._active = None
        self._improvement = float(context.config.get("min_improvement", 0.0))
        context.subscribe(EVENT_VALIDATED, self._on_validated)
        context.subscribe(EVENT_ACTIVE, self._on_active)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_active(self, p: dict) -> None:
        if self._running and isinstance(p, dict):
            self._active = dict(p)

    async def _on_validated(self, p: dict) -> None:
        if not self._running or self._context is None or not isinstance(p, dict):
            return
        old = float((self._active or {}).get("accuracy", 0.0))
        new = float(p.get("accuracy", 0.0))
        approved = bool(p.get("passed")) and (
            self._active is None or new >= old + self._improvement)
        self._seen += 1
        await self._context.publish(EVENT_OUT, {
            "candidate": dict(p),
            "approved": approved,
            "current_accuracy": old,
            "candidate_accuracy": new,
            "reason": "BETTER_OR_FIRST" if approved else "NOT_BETTER",
        })

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        if self._seen:
            return HealthStatus(
                state=HealthState.HEALTHY,
                message="comparisons=%d" % self._seen,
                details={"seen": self._seen, "active": bool(self._active)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="READY_AWAITING_FIRST_VALIDATED_MODEL | comparisons=0",
            details={"seen": self._seen, "active": bool(self._active)})
