from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from catchup import decide
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from storage_policy import enforce_limits

ATOM_VERSION = "5.1.0"
MAX_BUFFER_ROWS = 100000

_DB_TIMEOUT_S = 5.0
_BUSY_TIMEOUT_MS = 3000
_SECONDS_PER_DAY = 86400.0

EVENT_DAY = "SYS_DAY"
EVENT_PULSE = "SYS_SECOND"
EVENT_OUT = "storage.timeline_saved"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
REASON_NO_ACTIVITY = "NO_EVENTS_YET"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS timeline (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT,
    event_name   TEXT NOT NULL,
    account_id   TEXT,
    symbol       TEXT,
    occurred_at  REAL,
    payload_json TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_timeline_event ON timeline(event_name, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_timeline_account ON timeline(account_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_timeline_time ON timeline(occurred_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_timeline_event_id ON timeline(event_id) WHERE event_id IS NOT NULL",
)

_INSERT = (
    "INSERT OR IGNORE INTO timeline (event_id, event_name, account_id, symbol, occurred_at, payload_json)"
    " VALUES (?,?,?,?,?,?)"
)

_PRUNE = "DELETE FROM timeline WHERE occurred_at IS NOT NULL AND occurred_at < ?"


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
        self._db_path = ""
        self._watch_events: list[str] = []
        self._flush_size = 0
        self._retention_days = 0.0
        self._flush_interval_s = 5.0
        self._flush_task: asyncio.Task | None = None
        self._immediate_events: set[str] = set()
        self._max_rows = 0
        self._max_db_bytes = 0
        self._limit_state: dict[str, Any] = {}
        self._last_prune_success: float | None = None
        self._catchup_done = False
        self._catchup_verdict: dict[str, Any] = {}
        self._buffer: list[tuple] = []
        self._store_ready = False
        self._last_error = ""
        self.recorded_count = 0
        self.flushed_count = 0
        self.dropped_count = 0
        self.duplicate_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = str(cfg["db_path"])
        self._watch_events = [str(e) for e in cfg["watch_events"]]
        self._flush_size = int(cfg["flush_size"])
        self._retention_days = float(cfg["retention_days"])
        self._flush_interval_s = float(cfg.get("flush_interval_s", 5.0))
        self._immediate_events = {str(e) for e in cfg.get("immediate_events", [])}
        self._max_rows = int(cfg.get("max_rows", 0))
        self._max_db_bytes = int(cfg.get("max_db_bytes", 0))
        for event_name in self._watch_events:
            context.subscribe(event_name, self._make_recorder(event_name))
        context.subscribe(EVENT_DAY, self._on_day)
        context.subscribe(EVENT_PULSE, self._on_pulse)
        self._store_ready = self._ensure_store()
        self._initialized = True

    async def start(self) -> None:
        if not self._initialized or self._running or self._context is None:
            return
        self._catchup_done = False
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._running = False
        task, self._flush_task = self._flush_task, None
        if task is not None:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        if self._buffer:
            await asyncio.to_thread(self._flush)

    async def _flush_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._flush_interval_s)
                if self._running and self._buffer:
                    self.flushed_count += await asyncio.to_thread(self._flush)
                    await self._enforce_limits()
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        await self.stop()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=_DB_TIMEOUT_S)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _ensure_store(self) -> bool:
        try:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                connection.execute(_SCHEMA)
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(timeline)")}
                if "event_id" not in columns: connection.execute("ALTER TABLE timeline ADD COLUMN event_id TEXT")
                for statement in _INDEXES:
                    connection.execute(statement)
                connection.commit()
            finally:
                connection.close()
            self._last_error = ""
            return True
        except (sqlite3.Error, OSError) as exc:
            self._last_error = str(exc)
            return False

    def _flush(self) -> int:
        if not self._buffer:
            return 0
        rows, self._buffer = self._buffer, []
        try:
            connection = self._connect()
            try:
                before = connection.total_changes
                connection.executemany(_INSERT, rows)
                inserted = connection.total_changes - before
                connection.commit()
                self.duplicate_count += len(rows) - inserted
            finally:
                connection.close()
            self._last_error = ""
            return inserted
        except sqlite3.Error as exc:
            self._last_error = str(exc)
            self._buffer = rows + self._buffer
            if len(self._buffer) > MAX_BUFFER_ROWS:
                overflow = len(self._buffer) - MAX_BUFFER_ROWS
                self._buffer = self._buffer[overflow:]
                self.dropped_count += overflow
            return 0

    def _prune(self, now: float) -> int:
        if self._retention_days <= 0:
            return 0
        cutoff = now - self._retention_days * _SECONDS_PER_DAY
        try:
            connection = self._connect()
            try:
                cursor = connection.execute(_PRUNE, (cutoff,))
                connection.commit()
                return cursor.rowcount or 0
            finally:
                connection.close()
        except sqlite3.Error as exc:
            self._last_error = str(exc)
            return 0

    def _make_recorder(self, event_name: str):
        async def handler(payload: dict[str, Any]) -> None:
            await self._record(event_name, payload)
        return handler

    async def _record(self, event_name: str, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        if not self._store_ready:
            self._store_ready = self._ensure_store()
            if not self._store_ready:
                self.dropped_count += 1
                return
        event_id = None
        account_id = None
        symbol = None
        occurred = None
        if isinstance(payload, dict):
            event_id = str(payload.get("event_id") or "").strip() or None
            account_id = payload.get("account_id")
            symbol = payload.get("symbol")
            occurred = _to_float(payload.get("timestamp"))
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload_json = None
        self._buffer.append((
            event_id,
            event_name,
            str(account_id) if account_id else None,
            str(symbol) if symbol else None,
            occurred,
            payload_json,
        ))
        self.recorded_count += 1
        if event_name in self._immediate_events or len(self._buffer) >= self._flush_size:
            self.flushed_count += await asyncio.to_thread(self._flush)
            await self._enforce_limits()

    async def _enforce_limits(self) -> None:
        try:
            self._limit_state = await asyncio.to_thread(
                enforce_limits, self._db_path, "timeline",
                max_rows=self._max_rows, max_db_bytes=self._max_db_bytes)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._last_error = str(exc)

    async def _on_day(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        self.flushed_count += await asyncio.to_thread(self._flush)
        now = _to_float(payload.get("official_time", payload.get("timestamp")))
        pruned = 0
        if now is not None:
            self._last_error = ""
            pruned = await asyncio.to_thread(self._prune, now)
            if not self._last_error:
                self._last_prune_success = now
        await self._enforce_limits()
        body: dict[str, Any] = {
            "recorded": self.recorded_count, "flushed": self.flushed_count,
            "duplicates": self.duplicate_count,
            "pruned": pruned,
        }
        if now is not None:
            body["timestamp"] = now
        await self._context.publish(EVENT_OUT, body)

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if self._catchup_done or not self._running or not isinstance(payload, dict):
            return
        now = _to_float(payload.get("official_time"))
        if now is None:
            return
        self._catchup_done = True
        verdict = decide({"last_success": self._last_prune_success}, now)
        self._catchup_verdict = verdict
        if verdict["run"]:
            await self._on_day({"official_time": now})

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "buffer": list(self._buffer[-MAX_BUFFER_ROWS:]),
                "dropped_count": self.dropped_count, "recorded_count": self.recorded_count,
                "flushed_count": self.flushed_count,
                "last_prune_success": self._last_prune_success}

    async def restore(self, state: dict[str, Any]) -> None:
        rows = state.get("buffer") if isinstance(state, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, (list, tuple)) for row in rows):
            raise ValueError("INVALID_STORAGE_BUFFER_STATE")
        self._buffer = [tuple(row) for row in rows[-MAX_BUFFER_ROWS:]]
        self.dropped_count = int(state.get("dropped_count") or 0)
        self.recorded_count = int(state.get("recorded_count") or 0)
        self.flushed_count = int(state.get("flushed_count") or 0)
        self._last_prune_success = _to_float(state.get("last_prune_success"))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {
            "recorded": self.recorded_count, "flushed": self.flushed_count,
            "duplicates": self.duplicate_count,
            "buffered": len(self._buffer), "dropped": self.dropped_count,
            "watching": len(self._watch_events), "db_path": self._db_path,
            "last_error": self._last_error, "limits": dict(self._limit_state),
            "last_prune_success": self._last_prune_success,
            "catchup": dict(self._catchup_verdict),
        }
        if not self._store_ready:
            return HealthStatus(
                state=HealthState.DEGRADED,
                message=self._last_error or REASON_STORE_UNAVAILABLE, details=details)
        if self._last_error:
            return HealthStatus(state=HealthState.DEGRADED, message=self._last_error, details=details)
        if self._limit_state.get("breached"):
            return HealthStatus(state=HealthState.DEGRADED, message="STORAGE_LIMIT_ENFORCED", details=details)
        if self.recorded_count == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NO_ACTIVITY, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="recorded=%d flushed=%d" % (self.recorded_count, self.flushed_count),
            details=details)
