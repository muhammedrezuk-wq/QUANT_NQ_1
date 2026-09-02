from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from catchup import decide, outcome

ATOM_VERSION = "3.0.2"

EVENT_PULSE = "SYS_SECOND"

_DB_TIMEOUT_S = 5.0
_BUSY_TIMEOUT_MS = 3000
_SECONDS_PER_DAY = 86400.0

EVENT_DAY = "SYS_DAY"
EVENT_OUT = "storage.archived"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_AWAITING_PULSE = "AWAITING_FIRST_PULSE"

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=_DB_TIMEOUT_S)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
    return connection


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._initialized = False
        self._running = False
        self._stores: list[dict[str, str]] = []
        self._archive_path = ""
        self._archive_after_days = 0
        self._batch_limit = 0
        self._runs = 0
        self._archived_total = 0
        self._last_error = ""
        self._last_success: float | None = None
        self._catchup_done = False
        self._catchup_verdict: dict[str, Any] = {}
        self._last_outcome: dict[str, Any] = {}
        self._last_report: dict[str, int] = {}
        self._last_closed_path: str | None = None
        self._rotation_counter = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._stores = [dict(s) for s in cfg["stores"]]
        self._archive_path = str(cfg["archive_db_path"])
        self._archive_after_days = int(cfg["archive_after_days"])
        self._batch_limit = int(cfg["batch_limit"])
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

    def _archive_one(self, store: dict[str, str], cutoff: float) -> int:
        source = store.get("db_path", ""); table = store.get("table", ""); time_col = store.get("time_column", "occurred_at")
        if not source or not _IDENT.fullmatch(table) or not _IDENT.fullmatch(time_col):
            raise ValueError("bad store spec")
        if not Path(source).is_file():
            raise FileNotFoundError(source)
        Path(self._archive_path).parent.mkdir(parents=True, exist_ok=True)
        connection = _connect(source)
        try:
            connection.execute("ATTACH DATABASE ? AS archive", (self._archive_path,))
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"CREATE TABLE IF NOT EXISTS archive.{table} AS SELECT * FROM main.{table} WHERE 0")
            ids = [row[0] for row in connection.execute(
                f"SELECT id FROM main.{table} WHERE {time_col} IS NOT NULL AND {time_col} < ? ORDER BY id LIMIT ?",
                (cutoff, self._batch_limit)).fetchall()]
            if not ids:
                connection.rollback(); return 0
            marks=','.join('?' for _ in ids)
            connection.execute(f"INSERT INTO archive.{table} SELECT * FROM main.{table} WHERE id IN ({marks})", ids)
            deleted=connection.execute(f"DELETE FROM main.{table} WHERE id IN ({marks})",ids).rowcount or 0
            if deleted != len(ids): raise sqlite3.DatabaseError("archive/delete count mismatch")
            connection.commit(); return deleted
        except BaseException:
            connection.rollback(); raise
        finally:
            connection.close()

    def _run(self, cutoff: float) -> dict[str, int]:
        self._last_error = ""
        report: dict[str, int] = {}
        for store in self._stores:
            moved = self._archive_one(store, cutoff)
            if moved:
                report[store.get("table", "?")] = moved
        return report

    @staticmethod
    def _verify_database(path: Path) -> None:
        connection = sqlite3.connect(path, timeout=_DB_TIMEOUT_S)
        try:
            verdict = connection.execute("PRAGMA integrity_check").fetchone()
            if not verdict or str(verdict[0]).lower() != "ok":
                raise sqlite3.DatabaseError("rotated archive integrity check failed")
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and int(checkpoint[0]) != 0:
                raise sqlite3.OperationalError("archive database is still busy")
        finally:
            connection.close()

    def _archive_row_count(self, path: Path) -> int:
        if not path.is_file():
            return 0
        connection = sqlite3.connect(path, timeout=_DB_TIMEOUT_S)
        try:
            total = 0
            for store in self._stores:
                table = str(store.get("table") or "")
                if not _IDENT.fullmatch(table):
                    continue
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists:
                    total += int(connection.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            return total
        finally:
            connection.close()

    def _closed_name(self, now: float) -> Path:
        active = Path(self._archive_path)
        suffix = active.suffix or ".db"
        stem = active.stem if active.suffix else active.name
        while True:
            self._rotation_counter += 1
            candidate = active.with_name(
                f"{stem}.closed.{int(now)}.{self._rotation_counter}{suffix}")
            if not candidate.exists():
                return candidate

    def _rotate_archive(self, now: float) -> tuple[str, int]:
        active = Path(self._archive_path)
        if not active.is_file():
            raise FileNotFoundError(self._archive_path)
        self._verify_database(active)
        closed_rows = self._archive_row_count(active)
        if closed_rows <= 0:
            raise sqlite3.DatabaseError("refusing to rotate an empty archive")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(active) + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise sqlite3.OperationalError("active archive sidecar remains: " + str(sidecar))
        closed = self._closed_name(now)
        closed.parent.mkdir(parents=True, exist_ok=True)
        os.replace(active, closed)
        try:
            connection = sqlite3.connect(active, timeout=_DB_TIMEOUT_S)
            connection.close()
            self._verify_database(closed)
        except BaseException:
            Path(active).unlink(missing_ok=True)
            os.replace(closed, active)
            raise
        self._last_closed_path = str(closed)
        return str(closed), closed_rows

    async def _on_day(self, payload: dict[str, Any]) -> None:
        now = _to_float(payload.get("official_time", payload.get("timestamp")))
        await self._archive(now, "SYS_DAY")

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if self._catchup_done or not self._running:
            return
        now = _to_float((payload or {}).get("official_time"))
        if now is None:
            return
        self._catchup_done = True
        verdict = decide({"last_success": self._last_success}, now)
        self._catchup_verdict = verdict
        if verdict["run"]:
            await self._archive(now, verdict["status"])

    async def _archive(self, now: float | None, trigger: str) -> None:
        if not self._running or self._context is None:
            return
        if now is None or self._archive_after_days <= 0:
            return
        cutoff = now - self._archive_after_days * _SECONDS_PER_DAY
        started = now
        closed_path: str | None = None
        closed_rows = 0
        report: dict[str, int] = {}
        moved = 0
        try:
            report = await asyncio.to_thread(self._run, cutoff)
            moved = sum(report.values())
            active_rows = await asyncio.to_thread(
                self._archive_row_count, Path(self._archive_path))
            if active_rows:
                closed_path, closed_rows = await asyncio.to_thread(
                    self._rotate_archive, now)
            copied = True
        except Exception as exc:
            self._last_error = str(exc)
            copied, closed_path, closed_rows = False, None, 0
        verified = copied and not self._last_error and (moved == 0 or closed_path is not None)
        result = outcome(copied, verified, self._source_intact(), started, now)
        if result["persist_last_success"]:
            self._last_success = result["last_success"]
        self._runs += 1
        self._archived_total += moved
        self._last_report = report
        self._last_outcome = result
        await self._context.publish(EVENT_OUT, {
            "rows": closed_rows, "moved_rows": moved,
            "total": self._archived_total, "per_table": dict(report),
            "cutoff": cutoff, "archive_path": closed_path, "timestamp": now,
            "active_archive_path": self._archive_path,
            "trigger": trigger, "status": result["status"],
            "last_success": self._last_success, "reason": result["reason"]})

    def _source_intact(self) -> bool:
        if not self._stores:
            return True
        return all(pathlib.Path(str(s.get("db_path") or "")).is_file()
                   for s in self._stores)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "last_success": self._last_success}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._last_success = _to_float(state.get("last_success"))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"runs": self._runs, "archived": self._archived_total,
                   "stores": len(self._stores), "last_report": dict(self._last_report),
                   "last_error": self._last_error,
                   "last_success": self._last_success,
                   "active_archive_path": self._archive_path,
                   "last_closed_path": self._last_closed_path,
                   "catchup": dict(self._catchup_verdict),
                   "last_outcome": dict(self._last_outcome)}
        if self._runs == 0:
            status = self._catchup_verdict.get("status")
            reason = self._catchup_verdict.get("reason")
            if status == "SKIPPED" and reason == "WITHIN_WINDOW":
                age = _to_float(self._catchup_verdict.get("age_s"))
                age_text = "%.1f" % (age / 3600.0) if age is not None else "?"
                return HealthStatus(
                    state=HealthState.HEALTHY,
                    message="READY - last successful archive %s hours ago at startup"
                            " (within 24h window) | WITHIN_WINDOW runs=0" % age_text,
                    details=details)
            if status == "SKIPPED":
                message = "SKIPPED_WINDOW %s" % (reason or "")
            elif status:
                message = str(status)
            else:
                message = (REASON_AWAITING_PULSE
                           + " - no official time pulse yet; catch-up decision unresolved")
            return HealthStatus(state=HealthState.DEGRADED,
                                message=message.strip(), details=details)
        if self._last_error:
            return HealthStatus(
                state=HealthState.DEGRADED, message=self._last_error, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="runs=%d archived=%d" % (self._runs, self._archived_total),
            details=details)
