from __future__ import annotations

from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.learning_model import (CLASSES, FEATURE_NAMES, FEATURE_SCHEMA_VERSION,
                                   schema_hash, stable_hash, train_softmax, valid_vector)
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"
SNAPSHOT_VERSION = 1
EVENT_IN = "learning.feature.ready"
EVENT_OUT = "learning.model.candidate"
MAX_MEMORY = 5000
MIN_TRAINING_SAMPLES = 3


@section_atom("350", "363")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._samples: list[dict[str, Any]] = []
        self._min_samples = 20
        self._validation_size = 5
        self._train_every = 1
        self._epochs = 100
        self._learning_rate = 0.05
        self._l2 = 0.001
        self._version = 0
        self._emitted = 0
        self._last_train_count = 0
        self._rejected = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._min_samples = max(MIN_TRAINING_SAMPLES, int(cfg.get("min_samples", 20)))
        self._validation_size = max(1, int(cfg.get("validation_size", 5)))
        self._train_every = max(1, int(cfg.get("train_every_new_samples", 1)))
        self._epochs = max(1, int(cfg.get("epochs", 100)))
        self._learning_rate = float(cfg.get("learning_rate", 0.05))
        self._l2 = float(cfg.get("l2", 0.001))
        context.subscribe(EVENT_IN, self._on_feature)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _valid(self, payload: dict[str, Any]) -> bool:
        return (payload.get("feature_schema_version") == FEATURE_SCHEMA_VERSION
                and payload.get("feature_schema_hash") == schema_hash()
                and payload.get("feature_names") == list(FEATURE_NAMES)
                and valid_vector(payload.get("feature_vector")) is not None
                and str(payload.get("label")) in CLASSES)

    async def _on_feature(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if not self._valid(payload):
            self._rejected += 1
            return
        self._samples.append(dict(payload))
        self._samples = self._samples[-(MAX_MEMORY + self._validation_size):]
        eligible = [row for row in self._samples if bool(row.get("training_eligible", True))]
        train = eligible[:-self._validation_size] if len(eligible) > self._validation_size else []
        if len(train) < self._min_samples:
            return
        if len(train) - self._last_train_count < self._train_every:
            return
        vectors = [valid_vector(row["feature_vector"]) for row in train]
        if any(row is None for row in vectors):
            self._rejected += 1
            return
        labels = [str(row["label"]) for row in train]
        artifact = train_softmax(vectors, labels, epochs=self._epochs,
                                 learning_rate=self._learning_rate, l2=self._l2)
        counts = {name: labels.count(name) for name in CLASSES}
        total = len(labels) + len(CLASSES)
        baseline = {"p_" + name: (counts[name] + 1) / total for name in CLASSES}
        self._version += 1
        self._emitted += 1
        self._last_train_count = len(train)
        dataset_fingerprint = stable_hash([
            {"sample_id": row.get("sample_id"), "label": row.get("label"),
             "feature_vector": row.get("feature_vector")} for row in train
        ])
        version = "candidate-%d-%s" % (self._version, dataset_fingerprint[:12])
        candidate = {
            **artifact,
            "model_version": version,
            "dataset_hash": dataset_fingerprint,
            "train_count": len(train),
            "train_ids": [row.get("sample_id") for row in train],
            "class_counts": counts,
            "baseline_probabilities": baseline,
            "training_config": {"epochs": self._epochs,
                                "learning_rate": self._learning_rate, "l2": self._l2},
            "status": "CANDIDATE",
        }
        # Hash includes the complete candidate except its own envelope hash.
        candidate["candidate_hash"] = stable_hash(candidate)
        await self._context.publish(EVENT_OUT, candidate)

    async def snapshot(self) -> dict[str, Any]:
        return {"snapshot_version": SNAPSHOT_VERSION, "samples": self._samples,
                "version": self._version, "emitted": self._emitted,
                "last_train_count": self._last_train_count, "rejected": self._rejected}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or state.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ValueError("INVALID_LEARNING_TRAINER_SNAPSHOT")
        samples = state.get("samples", [])
        if not isinstance(samples, list):
            raise ValueError("INVALID_LEARNING_TRAINER_SNAPSHOT")
        self._samples = [dict(row) for row in samples if isinstance(row, dict)][-MAX_MEMORY:]
        self._version = max(0, int(state.get("version", 0)))
        self._emitted = max(0, int(state.get("emitted", 0)))
        self._last_train_count = max(0, int(state.get("last_train_count", 0)))
        self._rejected = max(0, int(state.get("rejected", 0)))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"samples": len(self._samples), "candidates": self._emitted,
                   "rejected": self._rejected, "schema_hash": schema_hash()}
        return HealthStatus(state=HealthState.HEALTHY,
                            message="samples=%d candidates=%d" % (
                                len(self._samples), self._emitted), details=details)
