from __future__ import annotations

import time
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.learning_model import (CLASSES, TickFeatures, FEATURE_NAMES,
                                   FEATURE_SCHEMA_VERSION, predict, schema_hash,
                                   stable_hash)
from shared.section_contract import section_atom
from shared.tick_contract import VALIDATED_TICK_EVENT, as_validated_tick

ATOM_VERSION = "1.3.1"
# v1.3.1 (2026-08-27, item 14/27 of the 27-atom review -- "crash reading
# payload['data'] if the model is not found"): verified against current
# code, not assumed. _on_load_response already short-circuits on `not
# payload.get("found")` before ever touching "data" (via .get, not a
# bracket), and 706's own found=False response omits "data" entirely --
# so the described crash does not reproduce on this code. It also had
# ZERO test coverage. No behavior change; added a regression test and
# proved it load-bearing by temporarily reintroducing the exact bug
# (dropping the found-guard, switching to payload["data"]) -- reproduced
# the literal KeyError: 'data', then restored.
SNAPSHOT_VERSION = 2
EVENT_SELECTED = "learning.model.selected"
EVENT_ROLLBACK = "learning.model.rollback_requested"
EVENT_PERSIST = "model.persist_requested"
EVENT_PERSISTED = "model.persisted"
EVENT_LOAD = "storage.persistence.load_requested"
EVENT_LOAD_RESPONSE = "storage.persistence.load_response"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_ACTIVE = "learning.model.active.state"
EVENT_EVIDENCE = "learning.model.evidence"
MODEL_NAME = "learning_model"
MODES = ("disabled", "shadow", "advisory", "active")


def _artifact_hash(model: dict[str, Any]) -> str:
    keys = ("algorithm", "classes", "feature_schema_version", "feature_schema_hash",
            "feature_names", "means", "scales", "weights", "bias")
    return stable_hash({key: model.get(key) for key in keys})


def _model_valid(model: Any) -> bool:
    if not isinstance(model, dict) or not model.get("model_version"):
        return False
    if model.get("feature_schema_hash") != schema_hash():
        return False
    if model.get("feature_names") != list(FEATURE_NAMES):
        return False
    if model.get("artifact_hash") != _artifact_hash(model):
        return False
    try:
        predict(model, [0.0] * len(FEATURE_NAMES))
    except (TypeError, ValueError, OverflowError):
        return False
    return True


@section_atom("350", "367")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._mode = "shadow"
        self._active: dict[str, Any] | None = None
        self._pending: dict[str, Any] | None = None
        self._previous: list[dict[str, Any]] = []
        self._features = TickFeatures()
        self._selected = 0
        self._evidence = 0
        self._invalid = 0
        self._rollbacks = 0
        self._cooldown_s = 3600.0
        self._last_rollback_at = 0.0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        mode = str(context.config.get("mode", "shadow")).lower()
        if mode not in MODES:
            # Bad local configuration must not crash Core boot.  Fail closed:
            # keep the learner disabled and expose the fault through health.
            self._mode = "disabled"
            self._invalid += 1
        else:
            self._mode = mode
        self._cooldown_s = max(0.0, float(context.config.get(
            "rollback_cooldown_seconds", 3600.0)))
        context.subscribe(EVENT_SELECTED, self._on_selected)
        context.subscribe(EVENT_PERSISTED, self._on_persisted)
        context.subscribe(EVENT_LOAD_RESPONSE, self._on_load_response)
        context.subscribe(EVENT_ROLLBACK, self._on_rollback)
        context.subscribe(EVENT_TICK, self._on_tick)

    async def start(self) -> None:
        self._running = True
        if self._context is not None:
            await self._context.publish(EVENT_LOAD, {
                "request_id": "registry-load", "model_name": MODEL_NAME})

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _publish_active(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_ACTIVE, {
            **(self._active or {}), "active": bool(self._active),
            "mode": self._mode, "influence_enabled": self._mode == "active",
            "registry_size": len(self._previous) + (1 if self._active else 0),
        })

    async def _on_selected(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if not _model_valid(payload):
            self._invalid += 1
            return
        self._pending = dict(payload)
        self._selected += 1
        await self._context.publish(EVENT_PERSIST, {
            "request_id": "persist:" + str(payload["model_version"]),
            "model_name": MODEL_NAME,
            "version": payload["model_version"],
            "artifact_hash": payload["artifact_hash"],
            "data": dict(payload), "timestamp": payload.get("timestamp", 0),
        })

    async def _on_persisted(self, payload: dict[str, Any]) -> None:
        if (not self._running or not isinstance(payload, dict)
                or self._pending is None or payload.get("model_name") != MODEL_NAME):
            return
        if str(payload.get("version") or "") != str(self._pending.get("model_version")):
            return
        if self._active:
            self._previous.append(dict(self._active))
            self._previous = self._previous[-10:]
        self._active = self._pending
        self._pending = None
        await self._publish_active()

    async def _on_load_response(self, payload: dict[str, Any]) -> None:
        if (not self._running or not isinstance(payload, dict)
                or payload.get("model_name") != MODEL_NAME or not payload.get("found")):
            return
        data = payload.get("data")
        if not _model_valid(data):
            self._invalid += 1
            return
        self._active = dict(data)
        await self._publish_active()

    async def _on_rollback(self, payload: dict[str, Any]) -> None:
        if not self._running or not self._previous:
            return
        now = time.time()
        if now - self._last_rollback_at < self._cooldown_s:
            return
        requested = str(payload.get("model_version") or "") if isinstance(payload, dict) else ""
        if requested and self._active and requested != str(self._active.get("model_version")):
            return
        previous = self._previous.pop()
        if not _model_valid(previous):
            self._invalid += 1
            return
        self._active = previous
        self._last_rollback_at = now
        self._rollbacks += 1
        await self._publish_active()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        payload = as_validated_tick(payload)
        vector, _scope = self._features.build(payload)
        if vector is None or self._mode == "disabled":
            return
        probs = {"p_buy": 0.0, "p_sell": 0.0, "p_neutral": 1.0}
        state = "NOT_READY"
        version = None
        if self._active is not None:
            try:
                probs = predict(self._active, vector)
                version = self._active.get("model_version")
                state = "READY" if self._mode == "active" else self._mode.upper()
            except (TypeError, ValueError, OverflowError):
                self._invalid += 1
                state = "INVALID"
        direction = max(CLASSES, key=lambda name: probs["p_" + name])
        confidence = max(probs.values()) if self._active else 0.0
        self._evidence += 1
        await self._context.publish(EVENT_EVIDENCE, {
            "account_id": payload.get("account_id"),
            "broker": payload.get("broker"),
            "symbol": payload.get("symbol"),
            "timeframe": payload.get("timeframe"),
            "cycle_id": payload.get("cycle_id"),
            "period_start": payload.get("period_start", payload.get("timestamp")),
            "model_version": version,
            "direction": direction,
            **probs,
            "confidence": confidence,
            "mode": self._mode,
            "state": state,
            "influence_weight": 1.0 if self._mode == "active" else 0.0,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_schema_hash": schema_hash(),
            "feature_names": list(FEATURE_NAMES),
            "feature_vector": vector,
            "source": "learning_model",
        })

    async def snapshot(self) -> dict[str, Any]:
        return {
            "snapshot_version": SNAPSHOT_VERSION,
            "active": self._active, "pending": self._pending,
            "previous": self._previous, "features": self._features.snapshot(),
            "selected": self._selected, "evidence": self._evidence,
            "invalid": self._invalid, "rollbacks": self._rollbacks,
            "last_rollback_at": self._last_rollback_at,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or state.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ValueError("INVALID_LEARNING_REGISTRY_SNAPSHOT")
        active = state.get("active")
        pending = state.get("pending")
        previous = state.get("previous", [])
        self._active = dict(active) if _model_valid(active) else None
        self._pending = dict(pending) if _model_valid(pending) else None
        self._previous = [dict(row) for row in previous if _model_valid(row)][-10:] \
            if isinstance(previous, list) else []
        self._features.restore(state.get("features"))
        self._selected = max(0, int(state.get("selected", 0)))
        self._evidence = max(0, int(state.get("evidence", 0)))
        self._invalid = max(0, int(state.get("invalid", 0)))
        self._rollbacks = max(0, int(state.get("rollbacks", 0)))
        self._last_rollback_at = max(0.0, float(state.get("last_rollback_at", 0.0)))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"mode": self._mode, "active": bool(self._active),
                   "selected": self._selected, "evidence": self._evidence,
                   "invalid": self._invalid, "rollbacks": self._rollbacks}
        state = HealthState.DEGRADED if self._invalid else HealthState.HEALTHY
        return HealthStatus(state=state,
                            message="mode=%s active=%s evidence=%d" % (
                                self._mode, bool(self._active), self._evidence),
                            details=details)
