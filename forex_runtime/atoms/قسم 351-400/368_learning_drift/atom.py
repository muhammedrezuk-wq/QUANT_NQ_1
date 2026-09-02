from __future__ import annotations

import time
from collections import deque
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"
SNAPSHOT_VERSION = 1
EVENT_OUTCOME = "learning.outcome.recorded"
EVENT_ACTIVE = "learning.model.active.state"
EVENT_ROLLBACK = "learning.model.rollback_requested"
EVENT_STATE = "learning.model.drift.state"


@section_atom("350", "368")
class Atom(AtomBase):
    def __init__(self) -> None:
        # Campaign 1-449 batch B: dropped inputs are counted, never silent.
        self._dropped = 0
        self._context: AtomContext | None = None
        self._running = False
        self._active: dict[str, Any] | None = None
        self._windows: dict[str, deque[int]] = {}
        self._min_samples = 20
        self._min_accuracy = 0.45
        self._window_size = 200
        self._cooldown_s = 3600.0
        self._last_rollback: dict[str, float] = {}
        self._updates = 0
        self._rollbacks = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._min_samples = max(1, int(cfg.get("min_samples", 20)))
        self._min_accuracy = float(cfg.get("min_accuracy", 0.45))
        self._window_size = max(self._min_samples, int(cfg.get("window_size", 200)))
        self._cooldown_s = max(0.0, float(cfg.get("rollback_cooldown_seconds", 3600.0)))
        context.subscribe(EVENT_OUTCOME, self._on_outcome)
        context.subscribe(EVENT_ACTIVE, self._on_active)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_active(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict) and payload.get("active"):
            self._active = dict(payload)

    async def _on_outcome(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if not bool(payload.get("training_eligible", True)):
            self._dropped += 1
            return
        version = str(payload.get("model_version") or "")
        predicted = str(payload.get("model_direction") or "").lower()
        label = str(payload.get("outcome") or "neutral").lower()
        if not version or predicted not in ("buy", "sell", "neutral"):
            self._dropped += 1
            return
        window = self._windows.setdefault(version, deque(maxlen=self._window_size))
        window.append(int(predicted == label))
        accuracy = sum(window) / len(window)
        self._updates += 1
        status = "DRIFT" if len(window) >= self._min_samples and accuracy < self._min_accuracy else "STABLE"
        await self._context.publish(EVENT_STATE, {
            "model_version": version, "samples": len(window),
            "window_size": self._window_size, "accuracy": accuracy,
            "threshold": self._min_accuracy, "status": status,
        })
        active_version = str((self._active or {}).get("model_version") or "")
        now = time.time()
        allowed = now - self._last_rollback.get(version, 0.0) >= self._cooldown_s
        if status == "DRIFT" and active_version == version and allowed:
            self._last_rollback[version] = now
            self._rollbacks += 1
            await self._context.publish(EVENT_ROLLBACK, {
                "model_version": version, "reason": "MODEL_DRIFT",
                "samples": len(window), "accuracy": accuracy,
            })

    async def snapshot(self) -> dict[str, Any]:
        return {"snapshot_version": SNAPSHOT_VERSION, "active": self._active,
                "windows": {key: list(value) for key, value in self._windows.items()},
                "last_rollback": self._last_rollback,
                "updates": self._updates, "rollbacks": self._rollbacks}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or state.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ValueError("INVALID_LEARNING_DRIFT_SNAPSHOT")
        active = state.get("active")
        self._active = dict(active) if isinstance(active, dict) else None
        windows = state.get("windows", {})
        if not isinstance(windows, dict):
            raise ValueError("INVALID_LEARNING_DRIFT_SNAPSHOT")
        self._windows = {}
        for key, values in windows.items():
            if isinstance(values, list) and all(value in (0, 1) for value in values):
                self._windows[str(key)] = deque(values[-self._window_size:],
                                                  maxlen=self._window_size)
        previous = state.get("last_rollback", {})
        self._last_rollback = {str(k): float(v) for k, v in previous.items()} \
            if isinstance(previous, dict) else {}
        self._updates = max(0, int(state.get("updates", 0)))
        self._rollbacks = max(0, int(state.get("rollbacks", 0)))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"models": len(self._windows), "updates": self._updates,
                   "rollbacks": self._rollbacks}
        return HealthStatus(state=HealthState.HEALTHY,
                            message="drift_updates=%d rollbacks=%d" % (
                                self._updates, self._rollbacks), details=details)
