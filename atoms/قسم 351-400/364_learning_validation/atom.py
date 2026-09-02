from __future__ import annotations

import asyncio
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.learning_model import (CLASSES, classification_metrics, predict,
                                   schema_hash, stable_hash, valid_vector)
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.0"
# v1.2.0 (2026-08-27, item 11/27 of the 27-atom review -- the race the
# _on_feature comment below already documents was never actually guarded):
# _on_feature's retry loop snapshots self._pending, awaits _try_validate
# per candidate (a real suspension once a candidate is about to be
# published), then does self._pending.remove(candidate) by VALUE. A
# concurrent _on_candidate call for a RESUBMISSION of the same
# model_version (a legitimate case per the comment's own premise) filters
# self._pending and reassigns it while the retry is suspended -- the
# resubmission's dict differs from the original (e.g. updated train_ids),
# so the retry's remove() no longer finds an equal item and raises
# ValueError: list.remove(x): x not in list, crashing the handler task.
# Fixed with a single asyncio.Lock serializing _on_feature's and
# _on_candidate's shared-state sections against each other -- this atom
# has one flat feature/pending pool (no per-scope partitioning like 150),
# so one lock is the right granularity.
SNAPSHOT_VERSION = 2
EVENT_FEATURE = "learning.feature.ready"
EVENT_CANDIDATE = "learning.model.candidate"
EVENT_OUT = "learning.model.validated"
MAX_FEATURES = 5000
MAX_PENDING = 20


@section_atom("350", "364")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._features: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = []
        self._validation_size = 50
        self._min_accuracy = 0.55
        self._seen = 0
        self._emitted = 0
        self._rejected = 0
        self._state_lock = asyncio.Lock()

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._validation_size = max(1, int(cfg.get("validation_size", 50)))
        self._min_accuracy = float(cfg.get("min_accuracy", 0.55))
        context.subscribe(EVENT_FEATURE, self._on_feature)
        context.subscribe(EVENT_CANDIDATE, self._on_candidate)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_feature(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        # A trainer can publish while EventBus is still delivering this same
        # feature to subscribers.  Retry deferred candidates after the holdout
        # reaches us; event scheduling must not decide model validity.
        # v1.2.0: the lock serializes this whole section against
        # _on_candidate -- _try_validate awaits (publish) once a candidate
        # is about to complete, and self._pending must not be reassigned by
        # a concurrent resubmission out from under the remove() below.
        async with self._state_lock:
            self._features.append(dict(payload))
            self._features = self._features[-MAX_FEATURES:]
            for candidate in list(self._pending):
                if await self._try_validate(candidate):
                    self._pending.remove(candidate)

    async def _on_candidate(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._seen += 1
        candidate = dict(payload)
        async with self._state_lock:
            if not await self._try_validate(candidate):
                version = str(candidate.get("model_version") or "")
                self._pending = [row for row in self._pending
                                 if str(row.get("model_version") or "") != version]
                self._pending.append(candidate)
                self._pending = self._pending[-MAX_PENDING:]

    async def _try_validate(self, payload: dict[str, Any]) -> bool:
        if self._context is None:
            return False
        if payload.get("feature_schema_hash") != schema_hash():
            self._rejected += 1
            return True
        train_ids = set(payload.get("train_ids") or [])
        hold = [row for row in self._features
                if bool(row.get("training_eligible", True))
                and row.get("sample_id") not in train_ids]
        hold = hold[-self._validation_size:]
        # Not rejected: the holdout event may still be in EventBus delivery.
        if len(hold) < self._validation_size:
            return False
        vectors = [valid_vector(row.get("feature_vector")) for row in hold]
        labels = [str(row.get("label") or "") for row in hold]
        if any(row is None for row in vectors) or any(label not in CLASSES for label in labels):
            self._rejected += 1
            return True
        try:
            metrics = classification_metrics(payload, vectors, labels)
            counts = metrics["class_counts"]
            baseline = max(counts.values()) / len(labels)
            passed = (metrics["balanced_accuracy"] >= self._min_accuracy
                      and metrics["accuracy"] >= baseline)
            predict(payload, vectors[0])
        except (TypeError, ValueError, OverflowError):
            self._rejected += 1
            return True
        self._emitted += 1
        report = {
            **payload, "validation_size": len(hold), **metrics,
            "baseline_accuracy": baseline, "passed": passed,
            "validation_policy": "ordered_holdout_v2",
            "validation_ids": [row.get("sample_id") for row in hold],
            "status": "VALIDATED",
        }
        report["validation_hash"] = stable_hash(report)
        await self._context.publish(EVENT_OUT, report)
        return True

    async def snapshot(self) -> dict[str, Any]:
        return {"snapshot_version": SNAPSHOT_VERSION, "features": self._features,
                "pending": self._pending, "seen": self._seen,
                "emitted": self._emitted, "rejected": self._rejected}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or state.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ValueError("INVALID_LEARNING_VALIDATOR_SNAPSHOT")
        features = state.get("features", [])
        pending = state.get("pending", [])
        if not isinstance(features, list) or not isinstance(pending, list):
            raise ValueError("INVALID_LEARNING_VALIDATOR_SNAPSHOT")
        self._features = [dict(row) for row in features if isinstance(row, dict)][-MAX_FEATURES:]
        self._pending = [dict(row) for row in pending if isinstance(row, dict)][-MAX_PENDING:]
        self._seen = max(0, int(state.get("seen", 0)))
        self._emitted = max(0, int(state.get("emitted", 0)))
        self._rejected = max(0, int(state.get("rejected", 0)))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"features": len(self._features), "pending": len(self._pending),
                   "validated": self._emitted, "rejected": self._rejected}
        return HealthStatus(state=HealthState.HEALTHY,
                            message="validated=%d pending=%d rejected=%d" % (
                                self._emitted, len(self._pending), self._rejected),
                            details=details)
