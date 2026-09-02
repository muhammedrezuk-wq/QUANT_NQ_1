from __future__ import annotations

import shutil
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.1.0"

EVENT_PULSE = "SYS_5MIN"
EVENT_LOW = "tools.storage.low"
EVENT_RECOVERED = "tools.storage.recovered"

_DEFAULT_WARN_PCT = 80.0
_DEFAULT_CRITICAL_PCT = 90.0
_PERCENT = 100.0
_ROUND_DP = 2

_STATE_HEALTHY = "healthy"
_STATE_DEGRADED = "degraded"
_STATE_UNHEALTHY = "unhealthy"


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._path = "."
        self._warn_threshold_pct = _DEFAULT_WARN_PCT
        self._critical_threshold_pct = _DEFAULT_CRITICAL_PCT
        self._last_published_state = _STATE_HEALTHY
        self._last_health = HealthStatus(
            state=HealthState.DEGRADED, message="NOT_SAMPLED")
        self._last_payload: dict[str, Any] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._path = str(context.config.get("path", "."))
        self._warn_threshold_pct = float(context.config.get(
            "warn_threshold_pct", _DEFAULT_WARN_PCT))
        self._critical_threshold_pct = float(context.config.get(
            "critical_threshold_pct", _DEFAULT_CRITICAL_PCT))
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_pulse(self, _payload: dict[str, Any]) -> None:
        if not self._running:
            return
        try:
            usage = shutil.disk_usage(self._path)
        except OSError as exc:
            self._last_payload = {"path": self._path, "error": str(exc)}
            self._last_health = HealthStatus(
                state=HealthState.UNKNOWN,
                message=f"cannot read path {self._path}: {exc}",
                details=dict(self._last_payload))
            return
        used_pct = (usage.used / usage.total) * _PERCENT if usage.total > 0 else 0.0
        if used_pct >= self._critical_threshold_pct:
            new_state, health = _STATE_UNHEALTHY, HealthState.UNHEALTHY
        elif used_pct >= self._warn_threshold_pct:
            new_state, health = _STATE_DEGRADED, HealthState.DEGRADED
        else:
            new_state, health = _STATE_HEALTHY, HealthState.HEALTHY
        payload = {"path": self._path, "used_pct": round(used_pct, _ROUND_DP),
                   "used_bytes": usage.used, "total_bytes": usage.total}
        if self._context is not None:
            if (new_state != _STATE_HEALTHY
                    and self._last_published_state == _STATE_HEALTHY):
                await self._context.publish(EVENT_LOW, payload)
            elif (new_state == _STATE_HEALTHY
                  and self._last_published_state != _STATE_HEALTHY):
                await self._context.publish(EVENT_RECOVERED, payload)
        self._last_published_state = new_state
        self._last_payload = payload
        self._last_health = HealthStatus(
            state=health, message=f"disk usage: {used_pct:.1f}%",
            details=dict(payload))

    async def health_check(self) -> HealthStatus:
        return self._last_health

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION,
                "last_published_state": self._last_published_state,
                "last_payload": dict(self._last_payload),
                "last_health_state": self._last_health.state.value,
                "last_health_message": self._last_health.message}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_STORAGE_MONITOR_STATE")
        published = str(state.get("last_published_state") or "")
        if published not in {_STATE_HEALTHY, _STATE_DEGRADED, _STATE_UNHEALTHY}:
            raise ValueError("INVALID_STORAGE_MONITOR_STATE")
        self._last_published_state = published
        self._last_payload = dict(state.get("last_payload") or {})
        try:
            health = HealthState(str(state.get("last_health_state") or published))
        except ValueError as exc:
            raise ValueError("INVALID_STORAGE_MONITOR_STATE") from exc
        self._last_health = HealthStatus(
            state=health, message=str(state.get("last_health_message") or ""),
            details=dict(self._last_payload))
