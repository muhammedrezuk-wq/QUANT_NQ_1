from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
from typing import Any

from catchup import decide
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.2.2"

_SECONDS_PER_DAY = 86400.0

EVENT_DAY = "SYS_DAY"
EVENT_PULSE = "SYS_SECOND"
EVENT_DONE = "tools.backup.completed"
EVENT_FAILED = "tools.backup.failed"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NEVER_RAN = "NO_BACKUP_YET"


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
        self._backup_dir = ""
        self._source_dirs: list[str] = []
        self._keep_last_n = 1
        self._interval_days = 1
        self._last_success: float | None = None
        self._last_error = ""
        self._catchup_done = False
        self._catchup_verdict: dict[str, Any] = {}
        self.backup_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._backup_dir = str(cfg["backup_dir"])
        self._source_dirs = [str(s) for s in cfg["source_dirs"]]
        self._keep_last_n = int(cfg["keep_last_n"])
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

    def _run_backup(self, now: float) -> tuple[str, int, int]:
        backup_dir = Path(self._backup_dir).resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        sources = [Path(src).resolve() for src in self._source_dirs if Path(src).is_dir()]
        if len(sources) != len(self._source_dirs):
            raise FileNotFoundError("one or more backup sources are missing")
        archive = backup_dir / ("backup_%d.tar.gz" % int(now))
        fd, tmp_name = tempfile.mkstemp(
            dir=backup_dir, prefix=".backup.", suffix=".tar.gz.tmp")
        os.close(fd)
        file_count = 0
        try:
            with tempfile.TemporaryDirectory() as staging, tarfile.open(
                    tmp_name, "w:gz") as tar:
                stage = Path(staging)
                for source_index, src in enumerate(sources):
                    root_name = "%d_%s" % (source_index, src.name)
                    for root, dirs, files in os.walk(src):
                        root_path = Path(root).resolve()
                        dirs[:] = [d for d in dirs if not
                                   (root_path / d).resolve().is_relative_to(backup_dir)]
                        if root_path.is_relative_to(backup_dir):
                            continue
                        for name in files:
                            full = (root_path / name).resolve()
                            if full.is_relative_to(backup_dir):
                                continue
                            rel = full.relative_to(src)
                            arcname = (Path(root_name) / rel).as_posix()
                            if full.suffix.lower() == ".db":
                                staged = stage / (str(file_count) + ".db")
                                source_db = sqlite3.connect(
                                    full.as_uri() + "?mode=ro", uri=True, timeout=5)
                                target_db = sqlite3.connect(staged)
                                try:
                                    source_db.backup(target_db)
                                finally:
                                    target_db.close()
                                    source_db.close()
                                tar.add(staged, arcname=arcname)
                            else:
                                tar.add(full, arcname=arcname)
                            file_count += 1
            if file_count == 0:
                raise OSError("backup would be empty")
            with tarfile.open(tmp_name, "r:gz") as check:
                members = check.getmembers()
                if len(members) != file_count:
                    raise tarfile.TarError("backup member count mismatch")
            os.replace(tmp_name, archive)
            size = archive.stat().st_size
            self._enforce_retention()
            return str(archive), size, file_count
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _enforce_retention(self) -> None:
        try:
            backups = sorted(
                (f for f in os.listdir(self._backup_dir)
                 if f.startswith("backup_") and f.endswith(".tar.gz")), reverse=True)
        except OSError:
            return
        for old in backups[self._keep_last_n:]:
            try:
                os.remove(os.path.join(self._backup_dir, old))
            except OSError:
                pass

    def _due(self, now: float) -> bool:
        if self._last_success is None:
            return True
        age = now - self._last_success
        return age < 0 or age >= self._interval_days * _SECONDS_PER_DAY

    async def _execute(self, now: float, trigger: str) -> None:
        if not self._running or self._context is None or not self._due(now):
            return
        try:
            path, size, count = await asyncio.to_thread(self._run_backup, now)
        except (OSError, sqlite3.Error, tarfile.TarError) as exc:
            self._last_error = str(exc)
            await self._context.publish(EVENT_FAILED, {
                "reason": str(exc), "timestamp": now, "trigger": trigger})
            return
        self._last_success = now
        self._last_error = ""
        self.backup_count += 1
        await self._context.publish(EVENT_DONE, {
            "path": path, "size_bytes": size, "file_count": count,
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
                "backup_count": self.backup_count}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_BACKUP_STATE")
        value = state.get("last_success", state.get("last_run"))
        self._last_success = _to_float(value)
        self.backup_count = int(state.get("backup_count") or 0)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"backups": self.backup_count,
                   "last_success": self._last_success,
                   "backup_dir": self._backup_dir, "last_error": self._last_error,
                   "catchup": dict(self._catchup_verdict)}
        if self._last_error:
            return HealthStatus(
                state=HealthState.DEGRADED, message=self._last_error, details=details)
        if self.backup_count == 0:
            status = self._catchup_verdict.get("status")
            if status == "SKIPPED":
                message = ("READY - previous backup within window (%s) | backups=0"
                           % str(self._catchup_verdict.get("reason", "")).strip())
            else:
                message = "READY_AWAITING_FIRST_BACKUP_RUN | backups=0"
            return HealthStatus(
                state=HealthState.HEALTHY, message=message.strip(), details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="backups=%d" % self.backup_count, details=details)
