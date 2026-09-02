from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.0.0"

EVENT_IN = "liquidity.cycle.collected"
EVENT_OK = "liquidity.cycle.validated"
EVENT_FAIL = "liquidity.validation_failed"

ID_POOL = "pool"
ID_BUYSIDE = "buyside"
ID_SELLSIDE = "sellside"
STATUS_OK = "ok"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CYCLES = "NO_CYCLES_YET"

FAIL_POOL_PRICE = "pool_price_not_positive"
FAIL_LEVEL_PRICE = "liquidity_level_not_positive"


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _price(results: dict[str, Any], unit_id: str) -> float | None:
    state = results.get(unit_id)
    if not isinstance(state, dict) or state.get("status") != STATUS_OK:
        return None
    meta = state.get("metadata") or {}
    return _num(meta.get("price"))


@section_atom("250", "259")
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
        pool = _price(results, ID_POOL)
        if pool is not None and pool <= 0:
            return FAIL_POOL_PRICE
        for unit_id in (ID_BUYSIDE, ID_SELLSIDE):
            level = _price(results, unit_id)
            if level is not None and level <= 0:
                return FAIL_LEVEL_PRICE
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
