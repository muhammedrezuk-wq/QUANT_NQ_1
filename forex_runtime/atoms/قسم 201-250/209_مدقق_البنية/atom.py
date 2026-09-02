from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"

EVENT_IN = "structure.cycle.collected"
EVENT_OK = "structure.cycle.validated"
EVENT_FAIL = "structure.validation_failed"

ID_EXTERNAL = "external"
STATUS_OK = "ok"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CYCLES = "NO_CYCLES_YET"

FAIL_HIGH_NOT_POSITIVE = "external_high_not_positive"
FAIL_LOW_NOT_POSITIVE = "external_low_not_positive"
FAIL_HIGH_BELOW_LOW = "external_high_below_low"
FAIL_EXTERNAL_MISSING = "external_missing"
FAIL_EXTERNAL_NOT_OK = "external_not_ok"


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@section_atom("200", "209")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._seen = 0
        self._validated = 0
        self._failed = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_collected)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_collected(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._seen += 1
        results = payload.get("results") or {}
        reason = self._check(results)
        if reason is None:
            out = dict(payload)
            out["validated"] = True
            await self._context.publish(EVENT_OK, out)
            self._validated += 1
        else:
            await self._context.publish(EVENT_FAIL, {
                "cycle_id": str(payload.get("cycle_id", "")),
                "symbol": str(payload.get("symbol", "")), "reason": reason})
            self._failed += 1

    def _check(self, results: dict[str, Any]) -> str | None:
        external = results.get(ID_EXTERNAL)
        if not isinstance(external, dict):
            return FAIL_EXTERNAL_MISSING
        if external.get("status") != STATUS_OK:
            return FAIL_EXTERNAL_NOT_OK
        meta = external.get("metadata") or {}
        swing_high = _num(meta.get("swing_high"))
        swing_low = _num(meta.get("swing_low"))
        if swing_high is not None and swing_high <= 0:
            return FAIL_HIGH_NOT_POSITIVE
        if swing_low is not None and swing_low <= 0:
            return FAIL_LOW_NOT_POSITIVE
        if swing_high is not None and swing_low is not None and swing_high < swing_low:
            return FAIL_HIGH_BELOW_LOW
        return None

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CYCLES)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d validated=%d failed=%d" % (
                self._seen, self._validated, self._failed),
            details={"seen": self._seen, "validated": self._validated,
                     "failed": self._failed})
