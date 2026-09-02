from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from catchup import decide
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from storage_policy import enforce_limits

ATOM_VERSION = "2.4.0"

_DB_TIMEOUT_S = 5.0
_BUSY_TIMEOUT_MS = 3000
_SECONDS_PER_DAY = 86400.0
_ROW_WIDTH = 7

EVENT_IN = "portfolio.summary"
EVENT_OVERVIEW = "portfolio.overview.state"
EVENT_DAY = "SYS_DAY"
EVENT_PULSE = "SYS_SECOND"
EVENT_OUT = "storage.portfolio_saved"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
REASON_NOTHING_STORED = "NOTHING_STORED_YET"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   TEXT,
    equity       REAL,
    balance      REAL,
    realised_pnl REAL,
    open_count   INTEGER,
    occurred_at  REAL,
    payload_json TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_portfolio_account ON portfolio(account_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_time ON portfolio(occurred_at)",
)

_INSERT = (
    "INSERT INTO portfolio (account_id, equity, balance, realised_pnl, open_count,"
    " occurred_at, payload_json) VALUES (?,?,?,?,?,?,?)"
)

_PRUNE = "DELETE FROM portfolio WHERE occurred_at IS NOT NULL AND occurred_at < ?"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._initialized = False
        self._running = False
        self._db_path = ""
        self._min_interval_s = 0.0
        self._retention_days = 0
        self._max_rows = 0
        self._limit_state: dict[str, Any] = {}
        self._last_prune_success: float | None = None
        self._catchup_done = False
        self._catchup_verdict: dict[str, Any] = {}
        self._last_written: dict[str, float] = {}
        self._store_ready = False
        self._last_error = ""
        self.stored_count = 0
        self.skipped_count = 0
        self.dropped_count = 0
        self._pending: list[tuple] = []

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = str(cfg["db_path"])
        self._min_interval_s = float(cfg["min_write_interval_s"])
        self._retention_days = int(cfg["retention_days"])
        self._max_rows = int(cfg.get("max_rows", 0))
        context.subscribe(EVENT_IN, self._on_summary)
        context.subscribe(EVENT_OVERVIEW, self._on_summary)
        context.subscribe(EVENT_DAY, self._on_day)
        context.subscribe(EVENT_PULSE, self._on_pulse)
        self._store_ready = self._ensure_store()
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

    def _write_pending(self) -> int:
        if not self._pending: return 0
        rows = list(self._pending)
        try:
            connection = self._connect()
            try:
                connection.executemany(_INSERT, rows); connection.commit()
            finally: connection.close()
            del self._pending[:len(rows)]; self._last_error = ""; return len(rows)
        except sqlite3.Error as exc:
            self._last_error = str(exc); return 0

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

    async def _on_summary(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        raw_accounts = payload.get("accounts")
        accounts = raw_accounts if isinstance(raw_accounts, list) else [payload]
        accepted = 0
        for item in accounts:
            if not isinstance(item, dict): continue
            account_id = str(item.get("account_id") or "")
            if not account_id: self.dropped_count += 1; continue
            occurred = _to_float(item.get("timestamp", payload.get("timestamp")))
            if occurred is not None and account_id in self._last_written and occurred - self._last_written[account_id] < self._min_interval_s:
                self.skipped_count += 1; continue
            self._pending.append((account_id, _to_float(item.get("equity")),
                _to_float(item.get("balance")), _to_float(item.get("realised_pnl")),
                _to_int(item.get("open_count")), occurred,
                json.dumps(item, ensure_ascii=False, default=str)))
            if occurred is not None: self._last_written[account_id] = occurred
            accepted += 1
        if not accepted and not self._pending: return
        if not self._store_ready: self._store_ready = self._ensure_store()
        if not self._store_ready: return
        written = await asyncio.to_thread(self._write_pending)
        if not written: return
        self.stored_count += written
        try:
            self._limit_state = await asyncio.to_thread(
                enforce_limits, self._db_path, "portfolio", max_rows=self._max_rows)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._last_error = str(exc)
        await self._context.publish(EVENT_OUT, {"rows": written, "total": self.stored_count,
            "accounts": sorted(str(item.get("account_id")) for item in accounts
                               if isinstance(item, dict) and item.get("account_id")),
            "skipped": self.skipped_count})

    async def _on_day(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        now = _to_float(payload.get("official_time", payload.get("timestamp")))
        if now is None:
            return
        self._last_error = ""
        pruned = await asyncio.to_thread(self._prune, now)
        if not self._last_error:
            self._last_prune_success = now
        try:
            self._limit_state = await asyncio.to_thread(
                enforce_limits, self._db_path, "portfolio", max_rows=self._max_rows)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._last_error = str(exc)
        await self._context.publish(EVENT_OUT, {
            "rows": 0, "total": self.stored_count, "pruned": pruned, "timestamp": now})

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if self._catchup_done or not self._running or not isinstance(payload, dict): return
        now = _to_float(payload.get("official_time"))
        if now is None: return
        self._catchup_done = True
        verdict = decide({"last_success": self._last_prune_success}, now)
        self._catchup_verdict = verdict
        if verdict["run"]: await self._on_day({"official_time": now})

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "pending": [list(row) for row in self._pending],
                "last_written": dict(self._last_written), "stored": self.stored_count,
                "skipped": self.skipped_count, "dropped": self.dropped_count,
                "last_prune_success": self._last_prune_success}

    async def restore(self, state: dict[str, Any]) -> None:
        rows = state.get("pending") if isinstance(state, dict) else None
        if not isinstance(rows, list): raise ValueError("INVALID_PORTFOLIO_STORE_STATE")
        self._pending = [tuple(row) for row in rows if isinstance(row, list) and len(row) == _ROW_WIDTH]
        self._last_written = {str(k): float(v) for k,v in (state.get("last_written") or {}).items()}
        self.stored_count = int(state.get("stored") or 0); self.skipped_count = int(state.get("skipped") or 0); self.dropped_count = int(state.get("dropped") or 0)
        self._last_prune_success = _to_float(state.get("last_prune_success"))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"stored": self.stored_count, "skipped": self.skipped_count,
                   "dropped": self.dropped_count, "accounts": len(self._last_written),
                   "db_path": self._db_path, "last_error": self._last_error,
                   "limits": dict(self._limit_state),
                   "last_prune_success": self._last_prune_success,
                   "catchup": dict(self._catchup_verdict)}
        if not self._store_ready:
            return HealthStatus(
                state=HealthState.DEGRADED,
                message=self._last_error or REASON_STORE_UNAVAILABLE, details=details)
        if self._last_error:
            return HealthStatus(state=HealthState.DEGRADED, message=self._last_error, details=details)
        if self._limit_state.get("breached"):
            return HealthStatus(state=HealthState.DEGRADED, message="STORAGE_LIMIT_ENFORCED", details=details)
        if self.stored_count == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NOTHING_STORED, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="stored=%d skipped=%d" % (self.stored_count, self.skipped_count),
            details=details)
