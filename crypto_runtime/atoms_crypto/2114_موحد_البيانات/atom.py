from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "4.0.0"

EVENT_IN = "market_data.cleaned"
EVENT_OUT = "market_data.normalized"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "NO_DATA_YET"

_PREFIX = "market_data."


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self.normalized_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_cleaned)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_cleaned(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        source_event = str(payload.get("source_event", ""))
        if source_event.startswith(_PREFIX):
            data_type = source_event[len(_PREFIX):]
        else:
            data_type = source_event
        inner = payload.get("payload")
        if payload.get("validation_status") not in ("VALID", "SIDE_ONLY") or not isinstance(inner, dict):
            return
        out: dict[str, Any] = {"type": data_type, "source_event": source_event,
                               "validation_status": payload.get("validation_status"), "side_path_only": True, "data": dict(inner)}
        ts = inner.get("timestamp") if isinstance(inner, dict) else None
        if isinstance(ts, (int, float)):
            out["timestamp"] = ts
        self.normalized_count += 1
        await self._context.publish(EVENT_OUT, out)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self.normalized_count == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA)
        return HealthStatus(
            state=HealthState.HEALTHY, message="normalized=%d" % self.normalized_count)
