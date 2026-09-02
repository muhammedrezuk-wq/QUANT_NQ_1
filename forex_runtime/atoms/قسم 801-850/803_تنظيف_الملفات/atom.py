from __future__ import annotations

import asyncio
import fnmatch
import os
from typing import Any

from catchup import decide
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.1.0"

_SECONDS_PER_DAY = 86400.0

EVENT_DAY = "SYS_DAY"
EVENT_PULSE = "SYS_SECOND"
EVENT_DONE = "tools.file_cleanup.completed"
EVENT_FAILED = "tools.file_cleanup.failed"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NEVER_RAN = "NEVER_RAN"
REASON_NO_PATTERNS = "NO_PATTERNS_CONFIGURED"
REASON_INVALID_WINDOW = "INVALID_RETENTION_WINDOW"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._initialized = False
        self._running = False
        self._scan_dirs: list[str] = []
        self._older_than_days = 0
        self._patterns: list[str] = []
        self._interval_days = 0
        self._last_success: float | None = None
        self._last_error = ""
        self._catchup_done = False
        self._catchup_verdict: dict[str, Any] = {}
        self.run_count = 0
        self.deleted_total = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._scan_dirs = [str(s) for s in cfg["scan_dirs"]]
        self._older_than_days = int(cfg["older_than_days"])
        self._patterns = [str(p) for p in cfg["patterns"]]
        self._interval_days = int(cfg["interval_days"])
        context.subscribe(EVENT_DAY, self._on_day)
        context.subscribe(EVENT_PULSE, self._on_pulse)
        self._initialized = True

    async def start(self) -> None:
        if not self._initialized or self._running or self._context is None:
            return
        self._catchup_done = False
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _run_cleanup(self, now: float) -> int:
        if self._older_than_days <= 0:
            raise ValueError(REASON_INVALID_WINDOW)
        if not self._patterns:
            raise ValueError(REASON_NO_PATTERNS)
        missing = [path for path in self._scan_dirs if not os.path.isdir(path)]
        if missing:
            raise FileNotFoundError("cleanup paths missing: " + ",".join(missing))
        cutoff = now - self._older_than_days * _SECONDS_PER_DAY
        deleted = 0
        failures: list[str] = []
        for scan_dir in self._scan_dirs:
            for root, _dirs, files in os.walk(scan_dir):
                for name in files:
                    if not any(fnmatch.fnmatch(name, pat) for pat in self._patterns):
                        continue
                    path = os.path.join(root, name)
                    try:
                        if os.path.getmtime(path) < cutoff:
                            os.remove(path)
                            deleted += 1
                    except OSError as exc:
                        failures.append(f"{path}: {exc}")
        if failures:
            raise OSError("cleanup incomplete: " + "; ".join(failures))
        return deleted

    def _due(self, now: float) -> bool:
        if self._last_success is None:
            return True
        age = now - self._last_success
        return age < 0 or age >= self._interval_days * _SECONDS_PER_DAY

    async def _fail(self, now: float, trigger: str, reason: str) -> None:
        self._last_error = reason
        if self._context is not None:
            await self._context.publish(EVENT_FAILED, {
                "reason": reason, "timestamp": now, "trigger": trigger})

    async def _skip_no_patterns(self, now: float, trigger: str) -> None:
        self._last_error = REASON_NO_PATTERNS
        if self._context is not None:
            await self._context.publish(EVENT_DONE, {
                "deleted_count": 0, "total": self.deleted_total,
                "timestamp": now, "trigger": trigger,
                "reason": REASON_NO_PATTERNS,
                "last_success": self._last_success})

    async def _execute(self, now: float, trigger: str) -> None:
        if not self._running or self._context is None or not self._due(now):
            return
        if self._older_than_days <= 0:
            await self._fail(now, trigger, REASON_INVALID_WINDOW)
            return
        if not self._patterns:
            await self._skip_no_patterns(now, trigger)
            return
        try:
            deleted = await asyncio.to_thread(self._run_cleanup, now)
        except (OSError, ValueError) as exc:
            await self._fail(now, trigger, str(exc))
            return
        self._last_success = now
        self._last_error = ""
        self.run_count += 1
        self.deleted_total += deleted
        await self._context.publish(EVENT_DONE, {
            "deleted_count": deleted, "total": self.deleted_total,
            "timestamp": now, "trigger": trigger,
            "last_success": self._last_success})

    async def _on_day(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = _to_float(payload.get("official_time", payload.get("timestamp")))
        if now is not None:
            await self._execute(now, "SYS_DAY")

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if self._catchup_done or not self._running or not isinstance(payload, dict):
            return
        now = _to_float(payload.get("official_time"))
        if now is None:
            return
        self._catchup_done = True
        verdict = decide({"last_success": self._last_success}, now)
        self._catchup_verdict = verdict
        if verdict["run"]:
            await self._execute(now, verdict["status"])

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "last_success": self._last_success,
                "run_count": self.run_count, "deleted_total": self.deleted_total}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_FILE_CLEANUP_STATE")
        value = state.get("last_success", state.get("last_run"))
        self._last_success = _to_float(value)
        self.run_count = int(state.get("run_count") or 0)
        self.deleted_total = int(state.get("deleted_total") or 0)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"runs": self.run_count, "deleted": self.deleted_total,
                   "patterns": len(self._patterns),
                   "last_success": self._last_success,
                   "last_error": self._last_error,
                   "catchup": dict(self._catchup_verdict)}
        if self._older_than_days <= 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_INVALID_WINDOW,
                details=details)
        if not self._patterns:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NO_PATTERNS,
                details=details)
        if self._last_error:
            return HealthStatus(
                state=HealthState.DEGRADED, message=self._last_error, details=details)
        if self.run_count == 0:
            status = self._catchup_verdict.get("status")
            message = ("SKIPPED_WINDOW " + str(self._catchup_verdict.get("reason", ""))
                       if status == "SKIPPED" else REASON_NEVER_RAN)
            return HealthStatus(
                state=HealthState.DEGRADED, message=message.strip(), details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="runs=%d deleted=%d" % (self.run_count, self.deleted_total),
            details=details)
