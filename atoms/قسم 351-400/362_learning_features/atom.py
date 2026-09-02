from __future__ import annotations

from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.learning_model import (FEATURE_NAMES, FEATURE_SCHEMA_VERSION,
                                   schema_hash, valid_vector)
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"
SNAPSHOT_VERSION = 1
EVENT_IN = "learning.sample.ready"
EVENT_OUT = "learning.feature.ready"


@section_atom("350", "362")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._built = 0
        self._rejected = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_sample)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_sample(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        record = payload.get("record") if isinstance(payload.get("record"), dict) else {}
        evidence = record.get("model_evidence") if isinstance(record.get("model_evidence"), dict) else {}
        snapshot = record.get("feature_snapshot") if isinstance(record.get("feature_snapshot"), dict) else {}
        values = evidence.get("feature_vector", snapshot.get("feature_vector"))
        names = evidence.get("feature_names", snapshot.get("feature_names"))
        version = evidence.get("feature_schema_version", snapshot.get("feature_schema_version"))
        digest = evidence.get("feature_schema_hash", snapshot.get("feature_schema_hash"))
        vector = valid_vector(values)
        if (vector is None or names != list(FEATURE_NAMES)
                or version != FEATURE_SCHEMA_VERSION or digest != schema_hash()):
            self._rejected += 1
            return
        label = str(record.get("outcome") or "neutral").lower()
        if label not in ("buy", "sell", "neutral"):
            self._rejected += 1
            return
        self._built += 1
        await self._context.publish(EVENT_OUT, {
            "sample_id": payload.get("sample_id"),
            "dataset_version": payload.get("dataset_version"),
            "training_eligible": bool(payload.get("training_eligible", True)),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_schema_hash": schema_hash(),
            "feature_names": list(FEATURE_NAMES),
            "feature_vector": vector,
            "label": label,
            "label_policy_version": record.get("label_policy_version"),
            "feature_cutoff_time": record.get("feature_cutoff_time"),
            "label_time": record.get("label_time"),
        })

    async def snapshot(self) -> dict[str, Any]:
        return {"snapshot_version": SNAPSHOT_VERSION, "built": self._built,
                "rejected": self._rejected}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or state.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ValueError("INVALID_LEARNING_FEATURE_SNAPSHOT")
        self._built = max(0, int(state.get("built", 0)))
        self._rejected = max(0, int(state.get("rejected", 0)))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"built": self._built, "rejected": self._rejected,
                   "schema_hash": schema_hash()}
        state = HealthState.DEGRADED if self._rejected and not self._built else HealthState.HEALTHY
        return HealthStatus(state=state,
                            message="features=%d rejected=%d" % (self._built, self._rejected),
                            details=details)
