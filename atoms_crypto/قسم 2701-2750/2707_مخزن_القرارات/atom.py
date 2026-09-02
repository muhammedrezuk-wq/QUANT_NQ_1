from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from storage_policy import enforce_limits

ATOM_VERSION = "4.6.0"

_DB_TIMEOUT_S = 5.0
_BUSY_TIMEOUT_MS = 3000

EVENT_APPROVED = "decision.approved.state"
EVENT_TARGET = "perpetual.target.state"
EVENT_DISPATCH = "decision.dispatch.state"
EVENT_ORDER_REQUESTED = "execution.order.requested"
EVENT_ORDER_BUILT = "execution.order.built"
EVENT_ORDER_REJECTED = "execution.order.rejected"
EVENT_FINAL = "trading.final_decision"
EVENT_BRIDGE_WRITTEN = "platform.brain_signal.written"
EVENT_BRIDGE_FAILED = "platform.brain_signal.write_failed"
EVENT_ACK = "execution.command.ack"
EVENT_COMMAND_FAILED = "execution.command.failed"
EVENT_TRADE = "platform.trade_event"
# دفتر الفجوات بند ١١ (٢٠٢٦-٠٨-٢٨): حدثٌ كريبتويٌّ خاصٌّ لا مشترك — نسخة
# الفوركس من هذه الذرّة لا تستقبله أبدًا (لا ناشر له هناك)، بلا أثرٍ سلبيّ.
# `03-protocol.md` §٥ "السجل — journal.csv (ضمير المشروع)" حرفيًّا: "تُسجَّل
# كل إشارة مفعّلة: الحقيقية بنتائجها، والافتراضية التي كانت ستتفعل... القياس
# الصادق يحسب ما كان سيحدث لا ما نجونا منه صدفة." حتى v4.4.0 كانت هذه الذرّة
# تكتب فقط `decision.approved.state` (٢٢٧٦، المعتمَد النهائيّ) — أي مرشّحٍ
# رفضه ٢٢٧٥ (بوّابة اقتصادية/حلقة خارجية/توقّف يوميّ) كان يختفي كليًّا، لا
# سجلّ ولا حتى "كانت ستخسر". `crypto.decision.sized_entry.state`(٢٢٧٥) ينشر
# كل إشارةٍ صنّفها ٢٢٧٤ وأكّدتها محكمة الزناد فعليًّا (`approved`=true/false
# صراحةً بكل حالة) — هذا بالضبط "الإشارة المفعّلة" حرفيًّا، بصرف النظر عن
# مصيرها اللاحق.
EVENT_SIZED_ENTRY = "crypto.decision.sized_entry.state"
EVENT_OUT = "storage.decisions_saved"

STAGE_APPROVED = "APPROVED"
# عقد المحورين v1.1 §3-6: هدف 581 يُسجَّل بحقوله (risk_dial, base_target,
# gross_target, target_net, remaining_RB, dial_add_budget, remaining_add_budget)
# — الحمولة كاملة في payload_json.
STAGE_TARGET = "TARGET_STATE"
STAGE_DISPATCH = "DISPATCH"
STAGE_ORDER_REQUESTED = "ORDER_REQUESTED"
STAGE_ORDER_BUILT = "ORDER_BUILT"
STAGE_ORDER_REJECTED = "ORDER_REJECTED"
STAGE_DECISION_FINALIZED = "DECISION_FINALIZED"
STAGE_QUEUED = "QUEUED_TO_BRIDGE"
STAGE_BRIDGE_FAILED = "BRIDGE_WRITE_FAILED"
STAGE_ACKNOWLEDGED = "BROKER_ACKNOWLEDGED"
STAGE_COMMAND_FAILED = "BROKER_COMMAND_FAILED"
STAGE_FILLED_OPEN = "FILLED_OPEN"
STAGE_FILLED_PARTIAL = "FILLED_PARTIAL"
STAGE_FILLED_CLOSED = "FILLED_CLOSED"
STAGE_CANDIDATE_EVALUATED = "CANDIDATE_EVALUATED"   # بند ١١ — كل إشارةٍ مفعّلة، معتمَدة أو مرفوضة

REASON_NOT_STARTED = "NOT_STARTED"
REASON_STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
REASON_NO_ACTIVITY = "NO_DECISIONS_YET"

_STAGES = (
    (EVENT_APPROVED, STAGE_APPROVED),
    (EVENT_TARGET, STAGE_TARGET),
    (EVENT_DISPATCH, STAGE_DISPATCH),
    (EVENT_ORDER_REQUESTED, STAGE_ORDER_REQUESTED),
    (EVENT_ORDER_BUILT, STAGE_ORDER_BUILT),
    (EVENT_ORDER_REJECTED, STAGE_ORDER_REJECTED),
    (EVENT_FINAL, STAGE_DECISION_FINALIZED),
    (EVENT_BRIDGE_WRITTEN, STAGE_QUEUED),
    (EVENT_BRIDGE_FAILED, STAGE_BRIDGE_FAILED),
    (EVENT_ACK, STAGE_ACKNOWLEDGED),
    (EVENT_COMMAND_FAILED, STAGE_COMMAND_FAILED),
    (EVENT_SIZED_ENTRY, STAGE_CANDIDATE_EVALUATED),
)

_ALLOWED_STAGES = frozenset(stage for _, stage in _STAGES) | {
    STAGE_FILLED_OPEN, STAGE_FILLED_PARTIAL, STAGE_FILLED_CLOSED,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stage        TEXT NOT NULL,
    request_id   TEXT,
    account_id   TEXT,
    symbol       TEXT,
    direction    TEXT,
    approved     INTEGER,
    reason       TEXT,
    confidence   REAL,
    strategy_id  TEXT,
    model_id     TEXT,
    volume       REAL,
    stop_loss    REAL,
    take_profit  REAL,
    take_profit_2 REAL,
    take_profit_runner REAL,
    entry_price  REAL,
    payload_json TEXT,
    decided_at   REAL,
    decision_id  TEXT,
    gate_request_id TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_decisions_request ON decisions(request_id)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_account ON decisions(account_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_stage ON decisions(stage, id DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_dedupe ON decisions(stage, account_id, request_id) WHERE request_id IS NOT NULL",
)

# T5 (tail item, seal 22): the two link columns a pre-existing live database
# lacks. Added via ALTER TABLE ADD COLUMN (NULL default, no data loss) only
# when PRAGMA table_info shows them missing -- safe to run on every startup,
# including a database that already has them (idempotent, no error).
_MIGRATION_COLUMNS = ("decision_id", "gate_request_id")
# entry_price (2026-08-28, crypto 2276 v1.1.0): REAL, not TEXT like the pair
# above -- kept as its own ALTER TABLE below instead of folding into
# _MIGRATION_COLUMNS, whose loop hardcodes "TEXT" affinity.
# take_profit_2 (2026-08-28, crypto 2276 v2.0.0 / signal card 2277): second
# target of the v3.1 signal card (`scalping/03-protocol.md` §3) -- same REAL
# migration as entry_price.
# take_profit_runner (2026-08-28, signal card 2277 v1.2.0 / gaps ledger #7):
# grade-A-only optional runner target beyond take_profit_2 ("راكض مسموح")
# -- NULL for grade B and for every pre-migration row (never invented).
_MIGRATION_COLUMNS_REAL = ("entry_price", "take_profit_2", "take_profit_runner")

_COLUMNS = ("stage", "request_id", "account_id", "symbol", "direction", "approved",
            "reason", "confidence", "strategy_id", "model_id", "volume", "stop_loss",
            "take_profit", "take_profit_2", "take_profit_runner", "entry_price", "payload_json", "decided_at",
            "decision_id", "gate_request_id")


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._initialized = False
        self._running = False
        self._db_path = ""
        self._keep_payload = True
        self._max_rows = 0
        self._max_db_bytes = 0
        self._limit_state: dict[str, Any] = {}
        self._store_ready = False
        self._last_error = ""
        self.stored_count = 0
        self.duplicate_count = 0
        self.failure_count = 0
        self._per_stage: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = str(cfg["db_path"])
        self._keep_payload = bool(cfg["keep_full_payload"])
        self._max_rows = int(cfg.get("max_rows", 0))
        self._max_db_bytes = int(cfg.get("max_db_bytes", 0))
        for event, stage in _STAGES:
            context.subscribe(event, self._make_writer(stage))
        context.subscribe(EVENT_TRADE, self._on_trade)
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
                existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(decisions)").fetchall()}
                for column in _MIGRATION_COLUMNS:
                    if column not in existing_columns:
                        connection.execute("ALTER TABLE decisions ADD COLUMN %s TEXT" % column)
                for column in _MIGRATION_COLUMNS_REAL:
                    if column not in existing_columns:
                        connection.execute("ALTER TABLE decisions ADD COLUMN %s REAL" % column)
                connection.execute("UPDATE decisions SET stage=? WHERE stage='SENT'", (STAGE_DECISION_FINALIZED,))
                duplicate_rows = connection.execute(
                    "SELECT COALESCE(SUM(c-1),0) FROM (SELECT COUNT(*) c FROM decisions WHERE request_id IS NOT NULL GROUP BY stage,account_id,request_id HAVING c>1)"
                ).fetchone()[0]
                connection.execute(
                    "DELETE FROM decisions WHERE request_id IS NOT NULL AND id NOT IN (SELECT MIN(id) FROM decisions WHERE request_id IS NOT NULL GROUP BY stage,account_id,request_id)"
                )
                self.duplicate_count += int(duplicate_rows or 0)
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
                "INSERT OR IGNORE INTO decisions (%s) VALUES (%s)" % (columns, marks),
                tuple(row.get(name) for name in _COLUMNS))
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()

    def _make_writer(self, stage: str):
        async def handler(payload: dict[str, Any]) -> None:
            await self._store(stage, payload)
        return handler

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        stage = {"OPENED": STAGE_FILLED_OPEN, "PARTIAL": STAGE_FILLED_PARTIAL,
                 "CLOSED": STAGE_FILLED_CLOSED}.get(str(payload.get("event_type") or "").upper())
        if stage is not None:
            await self._store(stage, payload)

    def _row(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        approved = payload.get("approved")
        if approved is None:
            approved = meta.get("approved")
        decided_at = _to_float(payload.get("timestamp"))
        payload_json = None
        if self._keep_payload:
            try:
                payload_json = json.dumps(payload, ensure_ascii=False,
                                          sort_keys=True, default=str)
            except (TypeError, ValueError):
                payload_json = None
        return {
            "stage": stage,
            "request_id": _text(payload.get("request_id")),
            "account_id": _text(payload.get("account_id")),
            "symbol": _text(payload.get("symbol")),
            "direction": _text(payload.get("direction") or payload.get("side")
                               or payload.get("bias") or meta.get("direction")),
            "approved": None if approved is None else int(bool(approved)),
            "reason": _text(payload.get("reason") or meta.get("reason")),
            "confidence": _to_float(payload.get("confidence")
                                    if payload.get("confidence") is not None
                                    else meta.get("confidence")),
            # T5: the decision-chain identity pair, wherever the stage's own
            # payload carries it; absent stays NULL (never invented).
            "decision_id": _text(payload.get("decision_id")),
            "gate_request_id": _text(payload.get("gate_request_id")),
            "strategy_id": _text(payload.get("strategy_id")),
            "model_id": _text(payload.get("model_id")),
            "volume": _to_float(payload.get("volume")),
            "stop_loss": _to_float(payload.get("stop_loss")),
            "take_profit": _to_float(payload.get("take_profit")),
            "take_profit_2": _to_float(payload.get("take_profit_2")),
            "take_profit_runner": _to_float(payload.get("take_profit_runner")),
            "entry_price": _to_float(payload.get("entry_price")),
            "payload_json": payload_json,
            "decided_at": decided_at,
        }

    async def _store(self, stage: str, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if stage not in _ALLOWED_STAGES:
            self.failure_count += 1; self._last_error = "INVALID_DECISION_STAGE"
            return
        if not self._store_ready:
            self._store_ready = self._ensure_store()
        if not self._store_ready:
            self.failure_count += 1
            return
        row = self._row(stage, payload)
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
                enforce_limits, self._db_path, "decisions",
                max_rows=self._max_rows, max_db_bytes=self._max_db_bytes)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._last_error = str(exc)
        self._per_stage[stage] = self._per_stage.get(stage, 0) + 1
        body: dict[str, Any] = {"stage": stage, "request_id": row["request_id"],
                                "account_id": row["account_id"], "symbol": row["symbol"]}
        if row["decided_at"] is not None:
            body["timestamp"] = row["decided_at"]
        await self._context.publish(EVENT_OUT, body)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "stored": self.stored_count,
                "failures": self.failure_count, "duplicates": self.duplicate_count, "per_stage": dict(self._per_stage)}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict): raise ValueError("INVALID_DECISION_STORE_STATE")
        self.stored_count=int(state.get("stored") or 0);self.failure_count=int(state.get("failures") or 0)
        self._per_stage={str(k):int(v) for k,v in (state.get("per_stage") or {}).items()}

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"stored": self.stored_count, "per_stage": dict(self._per_stage),
                   "failures": self.failure_count, "duplicates": self.duplicate_count, "store_ready": self._store_ready,
                   "last_error": self._last_error, "limits": dict(self._limit_state)}
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
                state=HealthState.DEGRADED, message=REASON_NO_ACTIVITY, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="stored=%d" % self.stored_count, details=details)
