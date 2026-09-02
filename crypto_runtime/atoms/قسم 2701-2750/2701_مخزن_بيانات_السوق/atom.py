from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from catchup import decide
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from storage_policy import enforce_limits

ATOM_VERSION = "2.2.0"
MAX_BUFFER_ROWS = 100000

_DB_TIMEOUT_S = 5.0
_BUSY_TIMEOUT_MS = 3000
_SECONDS_PER_DAY = 86400.0

EVENT_IN = "market_data.price_received"
EVENT_DAY = "SYS_DAY"
EVENT_PULSE = "SYS_SECOND"
EVENT_OUT = "storage.market_data_saved"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
REASON_NOTHING_STORED = "NOTHING_STORED_YET"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_data (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT,
    provider     TEXT,
    bid          REAL,
    ask          REAL,
    occurred_at  REAL,
    payload_json TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_market_data_symbol ON market_data(symbol, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_market_data_time ON market_data(occurred_at)",
)

_INSERT = (
    "INSERT INTO market_data (symbol, provider, bid, ask, occurred_at, payload_json)"
    " VALUES (?,?,?,?,?,?)"
)

_PRUNE = "DELETE FROM market_data WHERE occurred_at IS NOT NULL AND occurred_at < ?"


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
        self._flush_size = 0
        self._retention_days = 0
        self._flush_interval_s = 30.0
        self._flush_task: asyncio.Task | None = None
        self._max_rows = 0
        self._max_db_bytes = 0
        self._limit_state: dict[str, Any] = {}
        self._last_prune_success: float | None = None
        self._catchup_done = False
        self._catchup_verdict: dict[str, Any] = {}
        self._buffer: list[tuple] = []
        self._store_ready = False
        self._last_error = ""
        self.stored_count = 0
        self.dropped_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = str(cfg["db_path"])
        self._flush_size = int(cfg["flush_size"])
        self._retention_days = int(cfg["retention_days"])
        self._flush_interval_s = float(cfg.get("flush_interval_s", 30.0))
        self._max_rows = int(cfg.get("max_rows", 0))
        self._max_db_bytes = int(cfg.get("max_db_bytes", 0))
        context.subscribe(EVENT_IN, self._on_price)
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
                    await self._flush_and_report(None)
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
                connection.executemany(_INSERT, rows)
                connection.commit()
            finally:
                connection.close()
            self._last_error = ""
            return len(rows)
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

    async def _on_price(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        if not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        if not self._store_ready:
            self._store_ready = self._ensure_store()
            if not self._store_ready:
                self.dropped_count += 1
                return
        occurred = _to_float(payload.get("timestamp"))
        provider = payload.get("provider")
        self._buffer.append((
            str(symbol),
            str(provider) if provider else None,
            _to_float(payload.get("bid")),
            _to_float(payload.get("ask")),
            occurred,
            json.dumps(payload, ensure_ascii=False, default=str),
        ))
        if len(self._buffer) >= self._flush_size:
            await self._flush_and_report(occurred)

    async def _on_day(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        now = _to_float(payload.get("official_time", payload.get("timestamp")))
        await self._flush_and_report(now, prune_at=now)

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
            await self._flush_and_report(now, prune_at=now)

    async def _flush_and_report(
        self, stamp: float | None, prune_at: float | None = None
    ) -> None:
        if self._context is None:
            return
        written = await asyncio.to_thread(self._flush)
        self.stored_count += written
        pruned = 0
        if prune_at is not None:
            self._last_error = ""
            pruned = await asyncio.to_thread(self._prune, prune_at)
            if not self._last_error:
                self._last_prune_success = prune_at
        try:
            self._limit_state = await asyncio.to_thread(
                enforce_limits, self._db_path, "market_data",
                max_rows=self._max_rows, max_db_bytes=self._max_db_bytes)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._last_error = str(exc)
        if written == 0 and prune_at is None:
            return
        body: dict[str, Any] = {
            "rows": written, "total": self.stored_count, "pruned": pruned,
        }
        if stamp is not None:
            body["timestamp"] = stamp
        await self._context.publish(EVENT_OUT, body)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "buffer": list(self._buffer[-MAX_BUFFER_ROWS:]),
                "dropped_count": self.dropped_count, "stored_count": self.stored_count,
                "last_prune_success": self._last_prune_success}

    async def restore(self, state: dict[str, Any]) -> None:
        rows = state.get("buffer") if isinstance(state, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, (list, tuple)) for row in rows):
            raise ValueError("INVALID_STORAGE_BUFFER_STATE")
        self._buffer = [tuple(row) for row in rows[-MAX_BUFFER_ROWS:]]
        self.dropped_count = int(state.get("dropped_count") or 0)
        self.stored_count = int(state.get("stored_count") or 0)
        self._last_prune_success = _to_float(state.get("last_prune_success"))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {
            "stored": self.stored_count, "dropped": self.dropped_count,
            "buffered": len(self._buffer), "db_path": self._db_path,
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
        if self.stored_count == 0 and not self._buffer:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NOTHING_STORED, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="stored=%d buffered=%d" % (self.stored_count, len(self._buffer)),
            details=details)
