from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

_TIMEOUT_S = 5.0
_BUSY_MS = 3000

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS execution_request_ledger ("
    "account_id TEXT NOT NULL, request_id TEXT NOT NULL, broker TEXT NOT NULL, "
    "symbol TEXT NOT NULL, side TEXT, requested_price REAL, point REAL, "
    "tick_value REAL, tick_size REAL, spec_digest TEXT, payload_json TEXT NOT NULL, "
    "PRIMARY KEY(account_id, request_id))",
    "CREATE TABLE IF NOT EXISTS processed_trade_events ("
    "identity TEXT PRIMARY KEY, account_id TEXT NOT NULL, event_type TEXT NOT NULL, "
    "source_identity TEXT NOT NULL, payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS execution_outbox ("
    "output_id TEXT PRIMARY KEY, event_name TEXT NOT NULL, payload_json TEXT NOT NULL, "
    "status TEXT NOT NULL DEFAULT 'PENDING')",
    "CREATE TABLE IF NOT EXISTS durable_consumer_claims ("
    "consumer TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY(consumer, event_id))",
    "CREATE TABLE IF NOT EXISTS durable_consumer_state ("
    "consumer TEXT NOT NULL, scope_key TEXT NOT NULL, state_json TEXT NOT NULL, "
    "updated_event_id TEXT NOT NULL, PRIMARY KEY(consumer, scope_key))",
)


class Journal:
    def __init__(self, path: str) -> None:
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=_TIMEOUT_S)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=%d" % _BUSY_MS)
        return connection

    def ensure(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            for statement in _SCHEMA:
                connection.execute(statement)
            connection.commit()
        finally:
            connection.close()

    def remember_request(self, row: dict[str, Any]) -> bool:
        connection = self.connect()
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO execution_request_ledger "
                "(account_id,request_id,broker,symbol,side,requested_price,point,tick_value,tick_size,spec_digest,payload_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (row["account_id"], row["request_id"], row["broker"], row["symbol"],
                 row.get("side"), row.get("requested_price"), row.get("point"),
                 row.get("tick_value"), row.get("tick_size"), row.get("spec_digest"),
                 json.dumps(row.get("payload", {}), ensure_ascii=False, sort_keys=True, default=str)))
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()

    def request(self, account_id: str, request_id: str) -> dict[str, Any] | None:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT account_id,request_id,broker,symbol,side,requested_price,point,tick_value,tick_size,spec_digest,payload_json "
                "FROM execution_request_ledger WHERE account_id=? AND request_id=?",
                (account_id, request_id)).fetchone()
            if row is None:
                return None
            keys = ("account_id", "request_id", "broker", "symbol", "side",
                    "requested_price", "point", "tick_value", "tick_size", "spec_digest")
            result = dict(zip(keys, row[:-1])); result["payload"] = json.loads(row[-1])
            return result
        finally:
            connection.close()

    def commit_event(self, identity: str, account_id: str, event_type: str,
                     source_identity: str, source_payload: dict[str, Any],
                     outputs: list[tuple[str, str, dict[str, Any]]]) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO processed_trade_events "
                "(identity,account_id,event_type,source_identity,payload_json) VALUES (?,?,?,?,?)",
                (identity, account_id, event_type, source_identity,
                 json.dumps(source_payload, ensure_ascii=False, sort_keys=True, default=str)))
            if cursor.rowcount == 0:
                connection.rollback()
                return False
            for output_id, event_name, payload in outputs:
                body = dict(payload); body["event_id"] = output_id
                connection.execute(
                    "INSERT INTO execution_outbox (output_id,event_name,payload_json,status) VALUES (?,?,?,'PENDING')",
                    (output_id, event_name,
                     json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)))
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def pending_outputs(self, limit: int = 200) -> list[tuple[str, str, dict[str, Any]]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT output_id,event_name,payload_json FROM execution_outbox "
                "WHERE status='PENDING' ORDER BY rowid LIMIT ?", (int(limit),)).fetchall()
            return [(str(row[0]), str(row[1]), json.loads(row[2])) for row in rows]
        finally:
            connection.close()

    def mark_emitted(self, output_id: str) -> None:
        connection = self.connect()
        try:
            connection.execute("UPDATE execution_outbox SET status='EMITTED' WHERE output_id=?",
                               (output_id,))
            connection.commit()
        finally:
            connection.close()

    def claim_consumer(self, consumer: str, event_id: str) -> bool:
        connection = self.connect()
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO durable_consumer_claims (consumer,event_id) VALUES (?,?)",
                (consumer, event_id))
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()

    def consumer_states(self, consumer: str) -> dict[str, dict[str, Any]]:
        """Load the durable financial projection owned by one consumer."""
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT scope_key,state_json FROM durable_consumer_state WHERE consumer=?",
                (consumer,)).fetchall()
            return {str(scope_key): json.loads(body) for scope_key, body in rows}
        finally:
            connection.close()

    def save_consumer_state(self, consumer: str, scope_key: str,
                            state: dict[str, Any], event_id: str) -> None:
        """Durably replace an idempotent administrative projection."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO durable_consumer_state "
                "(consumer,scope_key,state_json,updated_event_id) VALUES (?,?,?,?) "
                "ON CONFLICT(consumer,scope_key) DO UPDATE SET "
                "state_json=excluded.state_json,updated_event_id=excluded.updated_event_id",
                (consumer, scope_key,
                 json.dumps(state, ensure_ascii=False, sort_keys=True, default=str), event_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reduce_consumer_event(
        self, identity: str, account_id: str, event_type: str,
        source_identity: str, source_payload: dict[str, Any], consumer: str,
        scope_key: str, initial_state: dict[str, Any], reducer: Any,
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically claim an input, reduce durable state, and enqueue effects.

        ``reducer`` receives a detached state dict and returns
        ``(new_state, outputs)``. Outputs use the same tuple shape as
        :meth:`commit_event`.  A duplicate returns the already committed state
        and never runs the reducer, so replay cannot apply money twice.
        """
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT state_json FROM durable_consumer_state "
                "WHERE consumer=? AND scope_key=?", (consumer, scope_key)).fetchone()
            state = json.loads(previous[0]) if previous else dict(initial_state)
            cursor = connection.execute(
                "INSERT OR IGNORE INTO durable_consumer_claims (consumer,event_id) VALUES (?,?)",
                (consumer, identity))
            if cursor.rowcount == 0:
                connection.rollback()
                return False, state
            connection.execute(
                "INSERT OR IGNORE INTO processed_trade_events "
                "(identity,account_id,event_type,source_identity,payload_json) VALUES (?,?,?,?,?)",
                (identity, account_id, event_type, source_identity,
                 json.dumps(source_payload, ensure_ascii=False, sort_keys=True, default=str)))
            new_state, outputs = reducer(dict(state))
            if not isinstance(new_state, dict) or not isinstance(outputs, list):
                raise ValueError("INVALID_CONSUMER_REDUCTION")
            connection.execute(
                "INSERT INTO durable_consumer_state "
                "(consumer,scope_key,state_json,updated_event_id) VALUES (?,?,?,?) "
                "ON CONFLICT(consumer,scope_key) DO UPDATE SET "
                "state_json=excluded.state_json,updated_event_id=excluded.updated_event_id",
                (consumer, scope_key,
                 json.dumps(new_state, ensure_ascii=False, sort_keys=True, default=str), identity))
            for output_id, event_name, payload in outputs:
                body = dict(payload); body["event_id"] = output_id
                connection.execute(
                    "INSERT INTO execution_outbox (output_id,event_name,payload_json,status) "
                    "VALUES (?,?,?,'PENDING')",
                    (output_id, event_name,
                     json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)))
            connection.commit()
            return True, new_state
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def counts(self) -> dict[str, int]:
        connection = self.connect()
        try:
            return {
                "requests": int(connection.execute(
                    "SELECT COUNT(*) FROM execution_request_ledger").fetchone()[0]),
                "processed": int(connection.execute(
                    "SELECT COUNT(*) FROM processed_trade_events").fetchone()[0]),
                "outbox_pending": int(connection.execute(
                    "SELECT COUNT(*) FROM execution_outbox WHERE status='PENDING'").fetchone()[0]),
            }
        finally:
            connection.close()
