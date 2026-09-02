from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.2"

EVENT_ALERT = "system.alert"
EVENT_STATE = "system.alert.state"
EVENT_RECOVERED = "system.alert.recovered"
EVENT_SWEEP = "SYS_5MIN"

_SEVERITY_CRITICAL = "critical"
_SEVERITY_WARNING = "warning"
_SEVERITY_INFO = "info"
_SEVERITIES = (_SEVERITY_CRITICAL, _SEVERITY_WARNING, _SEVERITY_INFO)

# source event -> (default severity, publisher atom id)
# publisher ids verified against each manifest `publishes` (2026-08-24)
_MONITORED: dict[str, tuple[str, int]] = {
    "storage.persistence.save_failed": (_SEVERITY_CRITICAL, 706),
    "tools.integrity.violation": (_SEVERITY_CRITICAL, 7),
    "tools.backup.failed": (_SEVERITY_CRITICAL, 800),
    "tools.file_archive.failed": (_SEVERITY_CRITICAL, 802),
    "tools.file_cleanup.failed": (_SEVERITY_CRITICAL, 803),
    "market_data.validation_failed": (_SEVERITY_CRITICAL, 112),
    "structure.validation_failed": (_SEVERITY_CRITICAL, 209),
    "liquidity.validation_failed": (_SEVERITY_CRITICAL, 259),
    "time.utc.stale": (_SEVERITY_CRITICAL, 608),
    "time.ntp.drift": (_SEVERITY_WARNING, 608),
    "financial.truth.shortage": (_SEVERITY_CRITICAL, 585),
    "core.integrity.alert": (_SEVERITY_CRITICAL, 1),
    "tools.storage.low": (_SEVERITY_WARNING, 6),
    "market_data.quality_alert": (_SEVERITY_WARNING, 115),
    "time.clock.divergence": (_SEVERITY_WARNING, 111),
    "storage.symbol.unmapped": (_SEVERITY_WARNING, 708),
}

_DEFAULT_COOLDOWN_S = 300.0
_DEFAULT_EXPIRY_S = 3600.0
_DEFAULT_STATE_FILE = "var/alerts/system_alerts.json"
_DETAIL_KEYS = ("error", "message", "reason", "detail")
_DETAIL_MAX = 300


def _extract_detail(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in _DETAIL_KEYS:
        value = payload.get(key)
        if value in (None, ""):
            continue
        text = str(value).strip()
        if not text:
            continue
        return text[:_DETAIL_MAX]
    return ""


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._cooldown_s = _DEFAULT_COOLDOWN_S
        self._expiry_s = _DEFAULT_EXPIRY_S
        self._state_file = _DEFAULT_STATE_FILE
        self._severity_overrides: dict[str, str] = {}
        self._active: dict[str, dict[str, Any]] = {}
        self._file_error: str | None = None
        self._last_health = HealthStatus(
            state=HealthState.UNKNOWN, message="NOT_INITIALIZED")

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        config = context.config
        self._cooldown_s = float(config.get(
            "cooldown_seconds", _DEFAULT_COOLDOWN_S))
        self._expiry_s = float(config.get("expiry_seconds", _DEFAULT_EXPIRY_S))
        self._state_file = str(config.get(
            "state_file", _DEFAULT_STATE_FILE))
        overrides = config.get("severity_overrides") or {}
        if isinstance(overrides, dict):
            self._severity_overrides = {
                str(event): str(sev)
                for event, sev in overrides.items()
                if str(sev) in _SEVERITIES and str(event) in _MONITORED
            }
        for event in _MONITORED:
            context.subscribe(event, self._handler_for(event))
        context.subscribe(EVENT_SWEEP, self._on_sweep)

    async def start(self) -> None:
        self._running = True
        if self._context is not None and self._active:
            await self._publish_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # ── events ───────────────────────────────────────────────────────────────

    def _handler_for(self, event: str):
        async def _handler(payload: dict[str, Any]) -> None:
            await self._on_failure(event, payload)
        return _handler

    def _severity(self, event: str) -> str:
        override = self._severity_overrides.get(event)
        if override:
            return override
        return _MONITORED.get(event, (_SEVERITY_INFO, 0))[0]

    async def _on_failure(self, event: str, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        now = time.time()
        record = self._active.get(event)
        if record is None:
            record = {"severity": self._severity(event), "count": 0,
                      "first_at": now, "last_at": now,
                      "last_emitted": 0.0, "detail": ""}
            self._active[event] = record
        record["count"] = int(record.get("count", 0)) + 1
        record["last_at"] = now
        detail = _extract_detail(payload)
        if detail:
            record["detail"] = detail
        if now - float(record.get("last_emitted", 0.0)) >= self._cooldown_s:
            record["last_emitted"] = now
            await self._context.publish(EVENT_ALERT, {
                "source_event": event,
                "source_atom": _MONITORED.get(event, (_SEVERITY_INFO, 0))[1],
                "severity": record["severity"],
                "count": record["count"],
                "first_at": record["first_at"],
                "last_at": now,
                "detail": record["detail"],
                "active_total": len(self._active),
                "atom_version": ATOM_VERSION,
            })
        await self._publish_state()

    async def _on_sweep(self, _payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        now = time.time()
        expired = sorted(
            event for event, record in self._active.items()
            if now - float(record.get("last_at", 0.0)) >= self._expiry_s)
        if not expired:
            return
        for event in expired:
            self._active.pop(event, None)
        await self._context.publish(EVENT_RECOVERED, {
            "recovered": expired,
            "total": len(self._active),
            "atom_version": ATOM_VERSION,
        })
        await self._publish_state()

    async def _publish_state(self) -> None:
        if self._context is None:
            return
        state = {
            "atom": self._context.atom_id,
            "atom_version": ATOM_VERSION,
            "total": len(self._active),
            "alerts": {
                event: {
                    "severity": record["severity"],
                    "source_atom": _MONITORED.get(event, (_SEVERITY_INFO, 0))[1],
                    "count": record["count"],
                    "first_at": record["first_at"],
                    "last_at": record["last_at"],
                    "detail": record["detail"],
                }
                for event, record in sorted(self._active.items())
            },
        }
        await self._context.publish(EVENT_STATE, state)
        self._persist(state)

    def _persist(self, state: dict[str, Any]) -> None:
        if self._context is None:
            return
        path = self._state_file
        try:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".system_alerts_", suffix=".tmp", dir=parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(state, fh, ensure_ascii=False)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            if self._file_error is not None:
                self._file_error = None
                self._context.logger.info(
                    "ALERT_STATE_FILE_RECOVERED path=%s", path)
        except OSError as exc:
            self._file_error = str(exc)
            self._context.logger.error(
                "ALERT_STATE_FILE_WRITE_FAILED path=%s error=%s", path, exc)

    # ── reporting / persistence ──────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        if self._context is None:
            return self._last_health
        active = len(self._active)
        if self._file_error is not None:
            self._last_health = HealthStatus(
                state=HealthState.DEGRADED,
                message=f"ALERT_STATE_FILE_WRITE_FAILED: {self._file_error}",
                details={"active": active})
        else:
            self._last_health = HealthStatus(
                state=HealthState.HEALTHY,
                message=f"{active} active alert(s)",
                details={"active": active})
        return self._last_health

    async def snapshot(self) -> dict[str, Any]:
        return {
            "version": ATOM_VERSION,
            "active": {event: dict(record) for event, record in
                       self._active.items()},
            "file_error": self._file_error,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_ALERT_AGGREGATOR_STATE")
        if state.get("version") != ATOM_VERSION:
            raise ValueError("INVALID_ALERT_AGGREGATOR_STATE")
        raw = state.get("active")
        if not isinstance(raw, dict):
            raise ValueError("INVALID_ALERT_AGGREGATOR_STATE")
        restored: dict[str, dict[str, Any]] = {}
        for event, record in raw.items():
            event = str(event)
            if event not in _MONITORED:
                continue
            if not isinstance(record, dict):
                raise ValueError("INVALID_ALERT_AGGREGATOR_STATE")
            severity = str(record.get("severity") or _SEVERITY_INFO)
            if severity not in _SEVERITIES:
                raise ValueError("INVALID_ALERT_AGGREGATOR_STATE")
            count = int(record.get("count", 0))
            first_at = float(record.get("first_at", 0.0))
            last_at = float(record.get("last_at", 0.0))
            last_emitted = float(record.get("last_emitted", 0.0))
            if min(count, first_at, last_at, last_emitted) < 0:
                raise ValueError("INVALID_ALERT_AGGREGATOR_STATE")
            restored[event] = {
                "severity": severity,
                "count": count,
                "first_at": first_at,
                "last_at": last_at,
                "last_emitted": last_emitted,
                "detail": str(record.get("detail") or "")[:_DETAIL_MAX],
            }
        self._active = restored
        file_error = state.get("file_error")
        self._file_error = str(file_error) if file_error else None
