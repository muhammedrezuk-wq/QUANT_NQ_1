from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tarfile
import tempfile
from typing import Any

from catchup import decide
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.2.1"

_SECONDS_PER_DAY = 86400.0

EVENT_DAY = "SYS_DAY"
EVENT_PULSE = "SYS_SECOND"
EVENT_DONE = "tools.file_archive.completed"
EVENT_FAILED = "tools.file_archive.failed"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NEVER_RAN = "NEVER_RAN"


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
        self._archive_dir = ""
        self._source_dirs: list[str] = []
        self._older_than_days = 0
        self._interval_days = 0
        self._last_success: float | None = None
        self._last_error = ""
        self._catchup_done = False
        self._catchup_verdict: dict[str, Any] = {}
        self.run_count = 0
        self.archived_total = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._archive_dir = str(cfg["archive_dir"])
        self._source_dirs = [str(s) for s in cfg["source_dirs"]]
        self._older_than_days = int(cfg["older_than_days"])
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

    def _collect_old(self, cutoff: float) -> list[tuple[str, str]]:
        archive_abs = os.path.abspath(self._archive_dir)
        found: list[tuple[str, str]] = []
        missing = [src for src in self._source_dirs if not os.path.exists(src)]
        if missing:
            raise FileNotFoundError("archive sources missing: " + ",".join(missing))
        for src in self._source_dirs:
            source_abs = os.path.abspath(src)
            if os.path.isfile(src):
                if (not source_abs.startswith(archive_abs + os.sep)
                        and os.path.getmtime(src) < cutoff):
                    found.append((src, os.path.basename(src)))
                continue
            root_name = os.path.basename(os.path.normpath(src))
            for root, _dirs, files in os.walk(src):
                root_abs = os.path.abspath(root)
                if root_abs == archive_abs or root_abs.startswith(archive_abs + os.sep):
                    continue
                for name in files:
                    path = os.path.join(root, name)
                    if os.path.getmtime(path) < cutoff:
                        arc = root_name + "/" + os.path.relpath(path, src)
                        found.append((path, arc.replace(os.sep, "/")))
        return found

    def _run_archive(self, now: float) -> tuple[int, int, str | None]:
        cutoff = now - self._older_than_days * _SECONDS_PER_DAY
        old = self._collect_old(cutoff)
        if not old:
            return 0, 0, None
        archive_dir = Path(self._archive_dir).resolve()
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive = archive_dir / ("archive_%d.tar.gz" % int(now))
        fd, tmp = tempfile.mkstemp(
            dir=archive_dir, prefix=".archive.", suffix=".tmp")
        os.close(fd)
        try:
            with tarfile.open(tmp, "w:gz") as tar:
                for path, arc in old:
                    tar.add(path, arcname=arc)
            with tarfile.open(tmp, "r:gz") as check:
                if len(check.getmembers()) != len(old):
                    raise tarfile.TarError("archive member count mismatch")
            os.replace(tmp, archive)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        removed = 0
        freed = 0
        failures: list[str] = []
        for path, _ in old:
            try:
                size = os.path.getsize(path)
                os.remove(path)
                removed += 1
                freed += size
            except OSError as exc:
                failures.append(f"{path}: {exc}")
        if failures:
            raise OSError("archive source removal incomplete: " + "; ".join(failures))
        return removed, freed, str(archive)

    def _due(self, now: float) -> bool:
        if self._last_success is None:
            return True
        age = now - self._last_success
        return age < 0 or age >= self._interval_days * _SECONDS_PER_DAY

    async def _execute(self, now: float, trigger: str) -> None:
        if (not self._running or self._context is None
                or self._older_than_days <= 0 or not self._due(now)):
            return
        try:
            count, freed, path = await asyncio.to_thread(self._run_archive, now)
        except (OSError, tarfile.TarError) as exc:
            self._last_error = str(exc)
            await self._context.publish(EVENT_FAILED, {
                "reason": str(exc), "timestamp": now, "trigger": trigger})
            return
        self._last_success = now
        self._last_error = ""
        self.run_count += 1
        self.archived_total += count
        await self._context.publish(EVENT_DONE, {
            "files_archived": count, "space_freed_bytes": freed,
            "archive_path": path, "total": self.archived_total,
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
                "run_count": self.run_count,
                "archived_total": self.archived_total}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_FILE_ARCHIVE_STATE")
        value = state.get("last_success", state.get("last_run"))
        self._last_success = _to_float(value)
        self.run_count = int(state.get("run_count") or 0)
        self.archived_total = int(state.get("archived_total") or 0)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"runs": self.run_count, "archived": self.archived_total,
                   "last_success": self._last_success,
                   "last_error": self._last_error,
                   "catchup": dict(self._catchup_verdict)}
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
            message="runs=%d archived=%d" % (self.run_count, self.archived_total),
            details=details)
