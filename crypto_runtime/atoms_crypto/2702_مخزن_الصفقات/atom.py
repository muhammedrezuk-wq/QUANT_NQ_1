from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from storage_policy import enforce_limits

ATOM_VERSION = "2.4.2"

_DB_TIMEOUT_S = 5.0
_BUSY_TIMEOUT_MS = 3000

EVENT_TRADE = "platform.trade_event"
EVENT_OUTCOME = "market.outcome.realized"
EVENT_OUT = "storage.trades_saved"

KIND_OPENED = "OPENED"
KIND_CLOSED = "CLOSED"
KIND_PARTIAL = "PARTIAL"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
REASON_NO_ACTIVITY = "NO_TRADES_YET"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,
    trade_id        TEXT,
    ticket          INTEGER,
    account_id      TEXT,
    symbol          TEXT,
    side            TEXT,
    size            REAL,
    entry_price     REAL,
    exit_price      REAL,
    opened_at       REAL,
    closed_at       REAL,
    reason          TEXT,
    strategy_id     TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    result          TEXT,
    partial         INTEGER,
    source_event_id INTEGER,
    stored_at       REAL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trades_ticket ON trades(ticket)",
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol, id DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_dedupe"
    " ON trades(kind, source_event_id) WHERE source_event_id IS NOT NULL",
)

_COLUMNS = ("kind", "trade_id", "ticket", "account_id", "symbol", "side", "size",
            "entry_price", "exit_price", "opened_at", "closed_at", "reason",
            "strategy_id", "pnl", "pnl_pct", "result", "partial",
            "source_event_id", "stored_at")


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
        self._store_ready = False
        self._last_error = ""
        self._max_rows = 0
        self._limit_state: dict[str, Any] = {}
        self.stored_count = 0
        self.duplicate_count = 0
        self.enriched_count = 0
        self.failure_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._db_path = str(context.config["db_path"])
        self._max_rows = int(context.config.get("max_rows", 0))
        context.subscribe(EVENT_TRADE, self._on_trade_event)
        context.subscribe(EVENT_OUTCOME, self._on_outcome)
        self._store_ready = self._ensure_store()
        self._initialized = True

    async def start(self) -> None:
        if not self._initialized or self._running or self._context is None:
            return
        self._running = True
        if not self._store_ready:
            self._store_ready = self._ensure_store()

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

    def _insert(self, row: dict[str, Any]) -> bool:
        connection = self._connect()
        try:
            columns = ", ".join(_COLUMNS)
            marks = ", ".join("?" for _ in _COLUMNS)
            cursor = connection.execute(
                "INSERT OR IGNORE INTO trades (%s) VALUES (%s)" % (columns, marks),
                tuple(row.get(name) for name in _COLUMNS))
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()

    def _enrich(self, ticket: int | None, trade_id: str | None, pnl: float | None,
                pnl_pct: float | None, result: str | None,
                strategy_id: str | None, source_event_id: int | None = None,
                account_id: str | None = None) -> int:
        if source_event_id is None and ticket is None and trade_id is None: return 0
        connection = self._connect()
        try:
            filters = ["kind IN (?, ?)"]; params: list[Any] = [KIND_CLOSED, KIND_PARTIAL]
            if source_event_id is not None:
                filters.append("source_event_id = ?"); params.append(source_event_id)
            elif trade_id:
                filters.append("trade_id = ?"); params.append(trade_id)
            else:
                filters.append("ticket = ?"); params.append(ticket)
                if account_id: filters.append("account_id = ?"); params.append(account_id)
            where = " AND ".join(filters)
            target = connection.execute("SELECT id FROM trades WHERE " + where + " ORDER BY id DESC LIMIT 1", params).fetchone()
            if target is None: return 0
            cursor = connection.execute(
                "UPDATE trades SET pnl=?, pnl_pct=?, result=?, strategy_id=COALESCE(?,strategy_id) WHERE id=?",
                (pnl, pnl_pct, result, strategy_id, target[0]))
            connection.commit(); return cursor.rowcount or 0
        finally: connection.close()

    async def _on_trade_event(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        kind = str(payload.get("event_type", ""))
        if kind not in (KIND_OPENED, KIND_CLOSED, KIND_PARTIAL):
            return
        await self._store(kind, payload)

    def _row(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": kind,
            "trade_id": payload.get("trade_id"),
            "ticket": _to_int(payload.get("ticket")),
            "account_id": str(payload.get("account_id"))
            if payload.get("account_id") else None,
            "symbol": payload.get("symbol"),
            "side": payload.get("side"),
            "size": _to_float(payload.get("volume")),
            "entry_price": _to_float(payload.get("entry_price")),
            "exit_price": _to_float(payload.get("exit_price")),
            "opened_at": _to_float(payload.get("open_time")),
            "closed_at": _to_float(payload.get("close_time")),
            "reason": payload.get("reason"),
            "strategy_id": str(payload.get("strategy_id"))
            if payload.get("strategy_id") else None,
            "pnl": _to_float(payload.get("profit")), "pnl_pct": None, "result": None,
            "partial": 1 if kind == KIND_PARTIAL else 0,
            "source_event_id": _to_int(payload.get("source_row_id")),
            "stored_at": _to_float(payload.get("timestamp")),
        }

    async def _store(self, kind: str, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if kind == KIND_CLOSED and payload.get("partial"):
            kind = KIND_PARTIAL
        if not self._store_ready:
            self._store_ready = self._ensure_store()
        if not self._store_ready:
            self.failure_count += 1
            return
        row = self._row(kind, payload)
        try:
            inserted = await asyncio.to_thread(self._insert, row)
        except sqlite3.Error as exc:
            self.failure_count += 1
            self._last_error = str(exc)
            return
        if not inserted:
            self.duplicate_count += 1
            return
        self.stored_count += 1
        try:
            self._limit_state = await asyncio.to_thread(
                enforce_limits, self._db_path, "trades", max_rows=self._max_rows)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._last_error = str(exc)
        body: dict[str, Any] = {"kind": kind, "ticket": row["ticket"],
                                "account_id": row["account_id"], "symbol": row["symbol"]}
        if row["stored_at"] is not None:
            body["timestamp"] = row["stored_at"]
        await self._context.publish(EVENT_OUT, body)

    async def _on_outcome(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if not self._store_ready:
            self._store_ready = self._ensure_store()
        if not self._store_ready:
            self.failure_count += 1
            return
        try:
            updated = await asyncio.to_thread(
                self._enrich, _to_int(payload.get("ticket")), payload.get("trade_id"),
                _to_float(payload.get("pnl", payload.get("profit"))), _to_float(payload.get("pnl_pct")),
                payload.get("result"),
                str(payload.get("strategy_id")) if payload.get("strategy_id") else None,
                _to_int(payload.get("source_row_id")),
                str(payload.get("account_id")) if payload.get("account_id") else None)
        except sqlite3.Error as exc:
            self.failure_count += 1
            self._last_error = str(exc)
            return
        self.enriched_count += updated

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "stored": self.stored_count,
                "duplicates": self.duplicate_count, "enriched": self.enriched_count,
                "failures": self.failure_count}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict): raise ValueError("INVALID_TRADE_STORE_STATE")
        self.stored_count = int(state.get("stored") or 0)
        self.duplicate_count = int(state.get("duplicates") or 0)
        self.enriched_count = int(state.get("enriched") or 0)
        self.failure_count = int(state.get("failures") or 0)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"stored": self.stored_count, "duplicates": self.duplicate_count,
                   "enriched": self.enriched_count, "failures": self.failure_count,
                   "store_ready": self._store_ready, "last_error": self._last_error,
                   "limits": dict(self._limit_state)}
        if not self._store_ready:
            return HealthStatus(
                state=HealthState.DEGRADED,
                message=self._last_error or REASON_STORE_UNAVAILABLE, details=details)
        if self._last_error:
            return HealthStatus(state=HealthState.DEGRADED, message=self._last_error, details=details)
        if self._limit_state.get("breached"):
            return HealthStatus(state=HealthState.DEGRADED, message="STORAGE_LIMIT_ENFORCED", details=details)
        if self.stored_count == 0:
            if self.failure_count:
                return HealthStatus(
                    state=HealthState.DEGRADED,
                    message="SUSPECTED_FAULT: %d store failures and zero rows stored" % self.failure_count,
                    details=details)
            return HealthStatus(
                state=HealthState.HEALTHY,
                message="READY_AWAITING_FIRST_TRADE_STORE | stored=0",
                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="stored=%d enriched=%d" % (self.stored_count, self.enriched_count),
            details=details)
