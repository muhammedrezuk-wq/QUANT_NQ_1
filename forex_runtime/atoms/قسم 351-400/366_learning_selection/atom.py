from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.0.5"

EVENT_IN = "learning.model.selection"
EVENT_OUT = "learning.model.selected"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_AWAITING = "READY_AWAITING_FIRST_APPROVED_MODEL | selected=0"

# 1.0.4 (2026-08-23): the selected model's activation stage is part of the
# contract -- a freshly selected model enters SHADOW by design (promotion to
# active is a separate governed step), and an upstream stage always wins.
STAGE_DEFAULT = "shadow"


@section_atom("350", "366")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._selected = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_selection)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_selection(self, payload: Any) -> None:
        if (not self._running or self._context is None
                or not isinstance(payload, dict) or not payload.get("approved")):
            return
        candidate = payload.get("candidate")
        candidate = candidate if isinstance(candidate, dict) else {}
        self._selected += 1
        stage = (payload.get("activation_stage")
                 or candidate.get("activation_stage")
                 or STAGE_DEFAULT)
        await self._context.publish(EVENT_OUT, {
            **candidate,
            "selected": True,
            "activation_stage": stage,
            "selection_reason": payload.get("reason"),
        })

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY,
                                message=REASON_NOT_STARTED)
        if self._selected:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="selected=%d" % self._selected,
                                details={"selected": self._selected})
        return HealthStatus(state=HealthState.HEALTHY, message=REASON_AWAITING,
                            details={"selected": self._selected})
