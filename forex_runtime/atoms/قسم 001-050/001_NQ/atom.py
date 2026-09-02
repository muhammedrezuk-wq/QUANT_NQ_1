from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.2.1"

EVENT_PULSE = "SYS_5MIN"
EVENT_PRESENCE = "NQ"
EVENT_INTEGRITY_ALERT = "core.integrity.alert"

_STATE_WATCHING = "watching"
_STATE_MISMATCH = "mismatch"

_DISTINCT_NAMES_CAP = 1000


def _text_field(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    if value is None:
        return None
    return str(value)


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._lock_path = "core/CORE.lock"
        self._last_known_digest: str | None = None
        self._last_known_version: str | None = None
        self._last_known_sealed_at: str | None = None
        self._alerts_raised = 0
        self._last_alert: dict[str, Any] | None = None
        self._last_health = HealthStatus(
            state=HealthState.DEGRADED, message="NOT_SAMPLED")
        self._eyes_active = False
        self._events_seen_total = 0
        self._distinct_event_names: set[str] = set()
        self._distinct_names_capped = False
        self._last_event_name: str | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._lock_path = str(context.config.get("core_lock_path", "core/CORE.lock"))
        context.subscribe(EVENT_PULSE, self._on_pulse)
        subscribe_all = getattr(context, "subscribe_all", None)
        if callable(subscribe_all):
            subscribe_all(self._on_any_event)
            self._eyes_active = True

    async def _on_any_event(self, event_name: str, payload: dict[str, Any]) -> None:
        self._events_seen_total += 1
        name = str(event_name)
        self._last_event_name = name
        if name not in self._distinct_event_names:
            if len(self._distinct_event_names) < _DISTINCT_NAMES_CAP:
                self._distinct_event_names.add(name)
            else:
                self._distinct_names_capped = True

    def _eyes_details(self) -> dict[str, Any]:
        return {
            "eyes_active": self._eyes_active,
            "events_seen_total": self._events_seen_total,
            "distinct_event_names": len(self._distinct_event_names),
            "distinct_names_capped": self._distinct_names_capped,
            "last_event_name": self._last_event_name,
        }

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _read_lock(self) -> dict[str, Any] | None:
        try:
            raw = Path(self._lock_path).read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or "root_digest" not in data:
            return None
        return data

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not self._running:
            return
        if self._context is not None:
            await self._context.publish(EVENT_PRESENCE, {
                "alerts_raised": self._alerts_raised,
                "watching": self._lock_path,
            })

        lock = self._read_lock()
        if lock is None:
            self._last_health = HealthStatus(
                state=HealthState.UNKNOWN,
                message=f"CANNOT_READ_CORE_LOCK: {self._lock_path}",
                details={"path": self._lock_path})
            return

        digest = _text_field(lock, "root_digest") or ""
        version = _text_field(lock, "core_version") or ""
        sealed_at = _text_field(lock, "sealed_at") or ""

        if self._last_known_digest is None:
            # First observation this process has ever made — either a genuine
            # first boot, or a restart with no prior snapshot to restore from.
            # Both cases must stay visible: publish the trust anchor being set
            # rather than adopting it in silence.
            alert = {
                "baseline_established": True,
                "old_digest": None, "new_digest": digest,
                "old_version": None, "new_version": version,
                "old_sealed_at": None, "new_sealed_at": sealed_at,
                "pulse_time": payload.get("official_time"),
            }
            self._last_alert = alert
            if self._context is not None:
                await self._context.publish(EVENT_INTEGRITY_ALERT, dict(alert))
            self._last_known_digest = digest
            self._last_known_version = version
            self._last_known_sealed_at = sealed_at
            self._last_health = HealthStatus(
                state=HealthState.HEALTHY,
                message=f"{_STATE_WATCHING}: {version} ({digest[:12]}…)",
                details={"core_version": version, "root_digest": digest})
            return

        digest_changed = digest != self._last_known_digest
        version_changed = version != self._last_known_version
        sealed_at_changed = sealed_at != self._last_known_sealed_at

        if digest_changed or version_changed or sealed_at_changed:
            self._alerts_raised += 1
            alert = {
                "baseline_established": False,
                "old_digest": self._last_known_digest, "new_digest": digest,
                "old_version": self._last_known_version, "new_version": version,
                "old_sealed_at": self._last_known_sealed_at, "new_sealed_at": sealed_at,
                "pulse_time": payload.get("official_time"),
            }
            self._last_alert = alert
            if self._context is not None:
                await self._context.publish(EVENT_INTEGRITY_ALERT, dict(alert))
            self._last_known_digest = digest
            self._last_known_version = version
            self._last_known_sealed_at = sealed_at
            changed = [name for name, flag in (
                ("digest", digest_changed), ("core_version", version_changed),
                ("sealed_at", sealed_at_changed)) if flag]
            self._last_health = HealthStatus(
                state=HealthState.DEGRADED,
                message=f"{_STATE_MISMATCH}: {'+'.join(changed)} changed",
                details=dict(alert))
            return

        self._last_health = HealthStatus(
            state=HealthState.HEALTHY,
            message=f"{_STATE_WATCHING}: {version} ({digest[:12]}…)",
            details={"core_version": version, "root_digest": digest,
                     "alerts_raised": self._alerts_raised})

    async def health_check(self) -> HealthStatus:
        details = dict(self._last_health.details or {})
        details.update(self._eyes_details())
        return HealthStatus(
            state=self._last_health.state,
            message=self._last_health.message,
            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {
            "version": ATOM_VERSION,
            "last_known_digest": self._last_known_digest,
            "last_known_version": self._last_known_version,
            "last_known_sealed_at": self._last_known_sealed_at,
            "alerts_raised": self._alerts_raised,
            "last_alert": dict(self._last_alert) if self._last_alert else None,
            "events_seen_total": self._events_seen_total,
            "distinct_event_names": sorted(self._distinct_event_names),
            "distinct_names_capped": self._distinct_names_capped,
            "last_event_name": self._last_event_name,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_NQ_STATE")
        digest = state.get("last_known_digest")
        if digest is not None and not isinstance(digest, str):
            raise ValueError("INVALID_NQ_STATE")
        version = state.get("last_known_version")
        if version is not None and not isinstance(version, str):
            raise ValueError("INVALID_NQ_STATE")
        sealed_at = state.get("last_known_sealed_at")
        if sealed_at is not None and not isinstance(sealed_at, str):
            raise ValueError("INVALID_NQ_STATE")
        alerts = state.get("alerts_raised", 0)
        if not isinstance(alerts, int) or isinstance(alerts, bool) or alerts < 0:
            raise ValueError("INVALID_NQ_STATE")
        last_alert = state.get("last_alert")
        if last_alert is not None and not isinstance(last_alert, dict):
            raise ValueError("INVALID_NQ_STATE")
        self._last_known_digest = digest
        self._last_known_version = version
        self._last_known_sealed_at = sealed_at
        self._alerts_raised = alerts
        self._last_alert = dict(last_alert) if last_alert else None
        seen = state.get("events_seen_total", 0)
        if not isinstance(seen, int) or isinstance(seen, bool) or seen < 0:
            raise ValueError("INVALID_NQ_STATE")
        names = state.get("distinct_event_names", [])
        if not isinstance(names, list):
            raise ValueError("INVALID_NQ_STATE")
        capped = state.get("distinct_names_capped", False)
        if not isinstance(capped, bool):
            raise ValueError("INVALID_NQ_STATE")
        last_name = state.get("last_event_name")
        if last_name is not None and not isinstance(last_name, str):
            raise ValueError("INVALID_NQ_STATE")
        self._events_seen_total = seen
        self._distinct_event_names = {str(n) for n in names[:_DISTINCT_NAMES_CAP]}
        self._distinct_names_capped = capped
        self._last_event_name = last_name
