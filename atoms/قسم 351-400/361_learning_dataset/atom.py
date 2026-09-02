from __future__ import annotations

from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.learning_model import stable_hash
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"
SNAPSHOT_VERSION = 1
EVENT_IN = "learning.outcome.recorded"
EVENT_OUT = "learning.sample.ready"
REQUIRED = ("account_id", "symbol", "direction", "outcome", "realized_pnl")
MAX_DEDUPE_KEYS = 20000


def _identity(payload: dict[str, Any]) -> str:
    raw = [payload.get("account_id"), payload.get("broker"), payload.get("symbol"),
           payload.get("decision_id"), payload.get("outcome_event_id"), payload.get("ticket")]
    return stable_hash([str(value or "") for value in raw])


@section_atom("350", "361")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._seen: list[str] = []
        self._seen_set: set[str] = set()
        self._samples = 0
        self._duplicates = 0
        self._rejected = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_record)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_record(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if any(key not in payload for key in REQUIRED) or not payload.get("symbol"):
            self._rejected += 1
            return
        sample_key = _identity(payload)
        if sample_key in self._seen_set:
            self._duplicates += 1
            return
        if not bool(payload.get("training_eligible", True)):
            self._rejected += 1
            return
        self._seen.append(sample_key)
        self._seen_set.add(sample_key)
        if len(self._seen) > MAX_DEDUPE_KEYS:
            removed = self._seen.pop(0)
            self._seen_set.discard(removed)
        self._samples += 1
        record = dict(payload)
        await self._context.publish(EVENT_OUT, {
            "sample_id": sample_key,
            "dataset_version": self._samples,
            "dataset_record_hash": stable_hash(record),
            "training_eligible": True,
            "record": record,
        })

    async def snapshot(self) -> dict[str, Any]:
        return {"snapshot_version": SNAPSHOT_VERSION, "seen": list(self._seen),
                "samples": self._samples, "duplicates": self._duplicates,
                "rejected": self._rejected}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or state.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ValueError("INVALID_LEARNING_DATASET_SNAPSHOT")
        seen = state.get("seen", [])
        if not isinstance(seen, list) or not all(isinstance(x, str) for x in seen):
            raise ValueError("INVALID_LEARNING_DATASET_SNAPSHOT")
        self._seen = seen[-MAX_DEDUPE_KEYS:]
        self._seen_set = set(self._seen)
        self._samples = max(0, int(state.get("samples", 0)))
        self._duplicates = max(0, int(state.get("duplicates", 0)))
        self._rejected = max(0, int(state.get("rejected", 0)))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"samples": self._samples, "duplicates": self._duplicates,
                   "rejected": self._rejected}
        message = "samples=%d duplicates=%d rejected=%d" % (
            self._samples, self._duplicates, self._rejected)
        return HealthStatus(state=HealthState.HEALTHY, message=message, details=details)
