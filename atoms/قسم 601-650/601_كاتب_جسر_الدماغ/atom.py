from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "4.2.0"
# v4.2.0 (2026-08-25): the result cursor is DURABLE. Measured gap: a cold
# boot baselined the cursor at MAX(done_at,id), so every EA result written
# while python was down (including the EA's own STALE_ON_STARTUP /
# UNKNOWN_AFTER_CRASH verdicts) was swallowed -- 578 never heard its leg
# fail across a restart. The durable cursor resumes exactly where the last
# life stopped; the snapshot cursor is still honored when it is FURTHER
# ahead (prevents the restore-lag double-publish window too).
_CURSOR_DB = "var/store/bridge_cursor_601.db"
_CURSOR_SCHEMA = ("CREATE TABLE IF NOT EXISTS cursor ("
                  " id INTEGER PRIMARY KEY CHECK (id = 1),"
                  " done_at REAL NOT NULL, last_id INTEGER NOT NULL)")

# NQ seal, item 22, package T (T1): the decision identity thread crosses the
# bridge INSIDE params_json -- an internal metadata column this writer already
# fills with a variable, optional-key JSON object. The agreed external table
# shape (columns) and every key the EA actually parses are untouched; the EA
# ignores keys it does not look for. Reading params_json back from our own
# rows lets the result events carry the same identity after any reboot.
IDENTITY_FIELDS = ("decision_id", "gate_request_id",
                   "parent_decision_id", "owner_command_id")

SUBSECOND_CLOCK_REASON = "bridge row write time must be real wall time, not a one-second pulse"
EVENT_FINAL_DECISION = "trading.final_decision"
EVENT_HALT = "emergency.halt"
EVENT_WRITTEN = "platform.brain_signal.written"
EVENT_WRITE_FAILED = "platform.brain_signal.write_failed"
EVENT_HALTED = "platform.brain_signal.halted"
EVENT_TIME = "SYS_SECOND"
EVENT_CMD_ACK = "execution.command.ack"
EVENT_CMD_FAILED = "execution.command.failed"
_RESULT_BATCH_LIMIT = 200
_RESULT_STATUS_OK = ("DONE",)
_RESULT_STATUS_FAILED = ("FAILED", "CANCELLED", "EXPIRED")
_BUSY_TIMEOUT_MS = 3000
_CONNECT_TIMEOUT_S = 5.0
_BEAT_FAILURES_BEFORE_FAULT = 3
PROJECT_BUILD_ID = "QUANT_NQ_FULL_212"
_SCHEMA = """CREATE TABLE IF NOT EXISTS commands (
 id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
 action TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT, volume REAL,
 price REAL, stop_loss REAL, take_profit REAL, ticket INTEGER,
 trail_dist REAL, trail_step REAL, params_json TEXT,
 magic INTEGER NOT NULL DEFAULT 0, account_id TEXT NOT NULL DEFAULT '', project_build_id TEXT,
 status TEXT NOT NULL DEFAULT 'PENDING', result TEXT,
 created_at REAL NOT NULL, taken_at REAL, done_at REAL)"""
_INDEX = "CREATE INDEX IF NOT EXISTS idx_cmd_status ON commands(status, id)"
_DISPLAY_SCHEMA = """CREATE TABLE IF NOT EXISTS display (
 id INTEGER PRIMARY KEY CHECK (id = 1), daily_pct REAL, wins INTEGER,
 losses INTEGER, trades INTEGER, open_trades INTEGER,
 kill_switch TEXT, updated_at REAL)"""
_DISPLAY_SEED = "INSERT OR IGNORE INTO display (id) VALUES (1)"
_HEARTBEAT_SQL = "UPDATE display SET updated_at = ? WHERE id = 1"
_INSERT_SQL = ("INSERT INTO commands (request_id, action, symbol, side, volume,"
               " price, stop_loss, take_profit, ticket, params_json, magic, account_id, project_build_id, status, created_at)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)")
_CANCEL_SQL = ("UPDATE commands SET status='CANCELLED', result='EMERGENCY_HALT',"
               " done_at=? WHERE status='PENDING' AND account_id=?")


def _resolve_db(configured: str) -> str:
    return os.environ.get("NQ_BRIDGE_DB", "").strip() or configured


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=_CONNECT_TIMEOUT_S)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return any(str(row[1]) == column for row in conn.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False


def _load_durable_cursor(path: str) -> tuple[float, int] | None:
    try:
        conn = _connect(path)
        try:
            conn.execute(_CURSOR_SCHEMA)
            row = conn.execute("SELECT done_at, last_id FROM cursor WHERE id = 1").fetchone()
        finally:
            conn.close()
        return (float(row[0]), int(row[1])) if row is not None else None
    except (sqlite3.Error, TypeError, ValueError, OSError):
        return None


def _save_durable_cursor(path: str, cursor: tuple[float, int]) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        conn = _connect(path)
        try:
            conn.execute(_CURSOR_SCHEMA)
            conn.execute("INSERT OR REPLACE INTO cursor (id, done_at, last_id)"
                         " VALUES (1, ?, ?)", (float(cursor[0]), int(cursor[1])))
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        pass  # التخزين لا يسقط النشر — الفشل يعيدنا لسلوك اللقطة فقط


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context = None
        self._account_id = ""
        self._configured_account_id = ""
        self._running = False
        self._current_account_mode = False
        self._blocked_account_ids: set[str] = set()
        self._db_path = "nq_brain.db"
        self._beat_interval_s = 0.0
        self._beat_task = None
        self.heartbeat_written = 0
        self.heartbeat_failed = 0
        self._beats_failing = 0
        self.written_count = 0
        self.halted_count = 0
        self.last_write_success = None
        self.last_write_at = None
        self.last_write_error = None
        self._detected_account_id = ""
        self._detected_account_ids: set[str] = set()
        self._account_mismatch = False
        self._result_cursor: tuple[float, int] | None = None
        self._cursor_reconciled = False
        self._results_busy = False
        self._magic = 0
        self.results_acked = 0
        self.results_failed = 0
        self.identity_incomplete = 0
        # م-34: مسار مؤشر قابل للضبط — الافتراضي كما كان، والاختبارات وقواعد
        # أخرى تعزله لكل قاعدة فلا يبتلع مؤشرُ قاعدةٍ نتائجَ أخرى.
        self._cursor_db = _CURSOR_DB

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        configured = str(context.config["account_id"]).strip()
        self._cursor_db = str(context.config.get("cursor_db") or _CURSOR_DB).strip() or _CURSOR_DB
        env_account = os.environ.get("NQ_ACCOUNT_ID", "").strip()
        self._configured_account_id = configured
        self._current_account_mode = not env_account and configured.upper() in {"CURRENT", "CURRENT_ACCOUNT", "AUTO_CURRENT"}
        self._require_symbol_resolution = bool(context.config.get("require_symbol_resolution", False))
        self._account_id = env_account or ("" if self._current_account_mode else configured)
        self._blocked_account_ids = {str(x).strip() for x in context.config.get("blocked_account_ids", []) if str(x).strip()}
        self._db_path = _resolve_db(str(context.config["db_path"]))
        self._beat_interval_s = float(context.config["heartbeat_interval_s"])
        self._magic = int(context.config.get("magic", 20260801))
        context.subscribe(EVENT_FINAL_DECISION, self._on_final_decision)
        context.subscribe(EVENT_HALT, self._on_emergency_halt)
        context.subscribe(EVENT_TIME, self._on_pulse)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            conn = _connect(self._db_path)
            try:
                conn.execute(_SCHEMA)
                if not _has_column(conn, "commands", "params_json"):
                    conn.execute("ALTER TABLE commands ADD COLUMN params_json TEXT")
                if not _has_column(conn, "commands", "magic"):
                    conn.execute("ALTER TABLE commands ADD COLUMN magic INTEGER NOT NULL DEFAULT 0")
                if not _has_column(conn, "commands", "account_id"):
                    conn.execute("ALTER TABLE commands ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
                if not _has_column(conn, "commands", "project_build_id"):
                    conn.execute("ALTER TABLE commands ADD COLUMN project_build_id TEXT")
                conn.execute(_INDEX)
                conn.execute(_DISPLAY_SCHEMA)
                conn.execute(_DISPLAY_SEED)
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            self.last_write_success = False
            self.last_write_error = str(exc)

    def _read_bridge_accounts(self) -> set[str] | None:
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True, timeout=_CONNECT_TIMEOUT_S)
            try:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_v2'").fetchone() is None:
                    return None
                return {str(row[0]) for row in conn.execute(
                    "SELECT account_id FROM account_v2 WHERE account_id IS NOT NULL AND account_id<>''").fetchall()
                    if str(row[0]) not in self._blocked_account_ids}
            finally: conn.close()
        except sqlite3.Error: return None

    def _sync_identity(self, detected: set[str] | None) -> bool:
        if detected is None:
            self._account_mismatch = True
            self.last_write_error = "BRIDGE_ACCOUNTS_UNAVAILABLE"
            return False
        self._detected_account_ids = set(detected)
        self._detected_account_id = next(iter(detected)) if len(detected) == 1 else ""
        if self._current_account_mode:
            self._account_mismatch = not bool(detected)
            if self._account_mismatch: self.last_write_error = "CURRENT_ACCOUNTS_UNAVAILABLE_OR_BLOCKED"
            return not self._account_mismatch
        self._account_mismatch = self._account_id not in detected or self._account_id in self._blocked_account_ids
        if self._account_mismatch: self.last_write_error = "BRIDGE_ACCOUNT_ID_MISMATCH_OR_BLOCKED"
        return not self._account_mismatch

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._sync_identity(await asyncio.to_thread(self._read_bridge_accounts))
        self._beat_task = asyncio.create_task(self._beat_loop())

    async def stop(self) -> None:
        self._running = False
        if self._beat_task is not None:
            self._beat_task.cancel()
            try: await self._beat_task
            except asyncio.CancelledError: pass
            self._beat_task = None

    async def shutdown(self) -> None:
        await self.stop()

    def _is_mine(self, account_id: Any) -> bool:
        account = str(account_id or "")
        if not account or account in self._blocked_account_ids: return False
        return account in self._detected_account_ids if self._current_account_mode else account == self._account_id

    def _metadata_json(self, payload: dict[str, Any]) -> str:
        fields = ("cycle_id", "origin", "pair_id", "leg_role", "attempt", "pair_required",
                  "protection_mode", "pair_volume", "purpose", "target_net", "current_net",
                  "delta_net", "risk_budget", "ticket", "logical_symbol", "broker_symbol",
                  "asset_canonical", "symbol_resolution_status", "snapshot_id") + IDENTITY_FIELDS
        data = {field: payload.get(field) for field in fields if payload.get(field) is not None}
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _identity_from_metadata(params_json: Any) -> dict[str, Any]:
        """Recover the identity the command row itself carries (durable across
        any reboot). Absent values pass as None -- never invented."""
        try:
            metadata = json.loads(params_json) if params_json else {}
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return {field: metadata.get(field) for field in IDENTITY_FIELDS}

    async def _on_final_decision(self, payload: dict) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        if not self._sync_identity(await asyncio.to_thread(self._read_bridge_accounts)):
            await self._record_failure(self.last_write_error or "ACCOUNT_ID_UNAVAILABLE", payload)
            return
        account_id = payload.get("account_id")
        if not self._is_mine(account_id):
            await self._record_failure("ACCOUNT_ID_MISMATCH" if account_id else "MISSING_ACCOUNT_ID", payload)
            return
        if self._require_symbol_resolution and payload.get("symbol_resolution_status") != "RESOLVED":
            await self._record_failure("SYMBOL_UNRESOLVED", payload)
            return
        request_id = str(payload.get("request_id") or "").strip()
        action = str(payload.get("action") or "OPEN").upper()
        if not request_id or action not in ("OPEN","CLOSE","CLOSE_PARTIAL","MODIFY_SL","MODIFY_TP","PENDING_CREATE","PENDING_DELETE"):
            await self._record_failure("MISSING_REQUEST_ID_OR_BAD_ACTION", payload)
            return
        try: command_magic=int(payload.get("magic"))
        except (TypeError,ValueError): command_magic=0
        if command_magic != self._magic:
            await self._record_failure("MISSING_OR_FOREIGN_MAGIC",payload)
            return
        symbol = payload.get("symbol")
        side = str(payload.get("side") or payload.get("bias") or "").upper()
        if not symbol or side not in ("BUY", "SELL"):
            await self._record_failure(f"bad symbol/side: symbol={symbol!r} side={side!r}", payload)
            return
        try: valid_volume = float(payload.get("volume")) > 0
        except (TypeError, ValueError): valid_volume = False
        if action not in ("CLOSE", "MODIFY_SL", "MODIFY_TP", "PENDING_DELETE") and not valid_volume:
            await self._record_failure(f"missing volume request_id={payload.get('request_id')!r}", payload)
            return
        if not any(payload.get(field) for field in IDENTITY_FIELDS):
            # T1 alert, tonight's style: the command still goes out (nothing is
            # blocked here -- 552 owns that verdict), but the gap is counted.
            self.identity_incomplete += 1
        row = (request_id, action,
               str(symbol), side, payload.get("volume"), payload.get("reference_price"),
               payload.get("stop_loss"), payload.get("take_profit"), payload.get("ticket"),
               self._metadata_json(payload), command_magic, str(account_id),
               str(payload.get("project_build_id") or PROJECT_BUILD_ID), time.time())
        try:
            await asyncio.to_thread(self._insert_command, row)
            self.written_count += 1
            self.last_write_success = True
            self.last_write_at = time.time()
            self.last_write_error = None
            await self._context.publish(EVENT_WRITTEN, {**payload, "side": side, "account_id": str(account_id),
                                                        "detected_account_id": str(account_id)})
        except sqlite3.Error as exc:
            await self._record_failure(str(exc), payload)

    def _insert_command(self, row: tuple) -> None:
        conn = _connect(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM commands WHERE account_id=? AND request_id=?", (row[11], row[0])).fetchone():
                raise sqlite3.IntegrityError("DUPLICATE_REQUEST_ID")
            conn.execute(_INSERT_SQL, row)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally: conn.close()

    async def _on_emergency_halt(self, payload: dict) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        target = str(payload.get("account_id") or "")
        accounts = ([target] if target and self._is_mine(target)
                    else sorted(self._detected_account_ids) if self._current_account_mode and not target
                    else [self._account_id] if not target and self._account_id else [])
        if not accounts: return
        self.halted_count += 1
        cancelled = 0
        try:
            for account in accounts: cancelled += await asyncio.to_thread(self._cancel_pending, account)
            self.last_write_at = time.time()
        except sqlite3.Error as exc: await self._record_failure(str(exc)); return
        await self._context.publish(EVENT_HALTED, {"account_ids": accounts,
            "account_id": target or None, "reason": payload.get("reason"), "cancelled_commands": cancelled})

    def _cancel_pending(self, account_id: str) -> int:
        conn = _connect(self._db_path)
        try: cursor = conn.execute(_CANCEL_SQL, (time.time(), account_id)); conn.commit(); return cursor.rowcount
        finally: conn.close()

    def _read_results(self) -> list[tuple]:
        accounts = sorted(self._detected_account_ids if self._current_account_mode else {self._account_id})
        if not accounts: return []
        marks = ",".join("?" for _ in accounts)
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True, timeout=_CONNECT_TIMEOUT_S)
        try:
            if self._result_cursor is None:
                # v4.2.0: المؤشر الدائم أولًا — يستأنف من حيث توقفت آخر حياة
                # فتُنشر نتائج فترة الغياب بدل ابتلاعها. أول تشغيل على
                # الإطلاق فقط يتأسس عند الذيل ويُثبت دائمًا.
                durable = _load_durable_cursor(self._cursor_db)
                if durable is not None:
                    self._result_cursor = durable
                else:
                    row = conn.execute("SELECT COALESCE(MAX(done_at),0),COALESCE(MAX(id),0) FROM commands"
                        f" WHERE done_at IS NOT NULL AND account_id IN ({marks})", accounts).fetchone()
                    self._result_cursor = (float(row[0] or 0.0), int(row[1] or 0))
                    _save_durable_cursor(self._cursor_db, self._result_cursor)
                    return []
            elif not self._cursor_reconciled:
                # لقطة مستعادة: الأبعد بين اللقطة والدائم يمنع إعادة النشر
                # المزدوج وابتلاع الفجوة معًا.
                durable = _load_durable_cursor(self._cursor_db)
                if durable is not None and durable > self._result_cursor:
                    self._result_cursor = durable
            self._cursor_reconciled = True
            done_at,last_id=self._result_cursor
            return conn.execute("SELECT id,request_id,action,symbol,side,volume,ticket,status,result,account_id,done_at,params_json FROM commands"
                f" WHERE done_at IS NOT NULL AND account_id IN ({marks}) AND (done_at>? OR (done_at=? AND id>?))"
                " ORDER BY done_at,id LIMIT ?", tuple(accounts)+(done_at,done_at,last_id,_RESULT_BATCH_LIMIT)).fetchall()
        finally: conn.close()

    async def _on_pulse(self, payload: dict) -> None:
        if not self._running or self._context is None or self._results_busy:
            return
        self._results_busy = True
        try:
            rows = await asyncio.to_thread(self._read_results)
        except sqlite3.Error:
            self._results_busy = False
            return
        try:
            for (row_id, request_id, action, symbol, side, volume, ticket,
                 status, result, account_id, done_at, params_json) in rows:
                self._result_cursor = (float(done_at or 0.0), int(row_id))
                body = {"request_id": str(request_id or ""), "action": action,
                        "symbol": symbol, "side": side, "volume": volume,
                        "ticket": ticket, "status": status, "result": result,
                        "account_id": account_id or self._account_id,
                        **self._identity_from_metadata(params_json)}
                if status in _RESULT_STATUS_OK:
                    self.results_acked += 1
                    await self._context.publish(EVENT_CMD_ACK, {
                        **body, "command_id": str(request_id or "")})
                elif status in _RESULT_STATUS_FAILED:
                    self.results_failed += 1
                    await self._context.publish(EVENT_CMD_FAILED, {
                        **body, "reason": str(result or status)})
                else:
                    self.results_failed += 1
                    await self._context.publish(EVENT_CMD_FAILED, {
                        **body, "reason": "UNKNOWN_RESULT_STATUS_%s" % str(status or "")})
            # v4.2.0: المؤشر يثبت دائمًا بعد كل دفعة منشورة.
            if rows and self._result_cursor is not None:
                _save_durable_cursor(self._cursor_db, self._result_cursor)
        finally:
            self._results_busy = False

    def _write_heartbeat(self, stamp: float) -> None:
        try:
            conn = _connect(self._db_path)
            try: conn.execute(_DISPLAY_SCHEMA); conn.execute(_DISPLAY_SEED); conn.execute(_HEARTBEAT_SQL, (float(stamp),)); conn.commit()
            finally: conn.close()
            self.heartbeat_written += 1
            self._beats_failing = 0
        except sqlite3.Error:
            self.heartbeat_failed += 1
            self._beats_failing += 1

    async def _beat_loop(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            while self._running:
                deadline += self._beat_interval_s
                if deadline <= loop.time(): deadline = loop.time() + self._beat_interval_s
                await asyncio.sleep(max(deadline - loop.time(), 0.0))
                self._sync_identity(await asyncio.to_thread(self._read_bridge_accounts))
                await asyncio.to_thread(self._write_heartbeat, time.time())
        except asyncio.CancelledError: pass

    async def _record_failure(self, reason: str, order: dict | None = None) -> None:
        self.last_write_success = False
        self.last_write_error = reason
        if self._context is None: return
        body: dict[str, Any] = {"account_id": (order or {}).get("account_id") or self._account_id or None, "reason": reason}
        if order:
            for field in ("request_id", "symbol", "side", "action", "volume", "pair_id",
                          "leg_role", "attempt") + IDENTITY_FIELDS:
                if order.get(field) is not None: body[field] = order[field]
        await self._context.publish(EVENT_WRITE_FAILED, body)
        self._context.logger.error("601 failed to write %s on %s: %s", body.get("request_id"), body.get("symbol"), reason)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "result_cursor": list(self._result_cursor) if self._result_cursor else None}
    async def restore(self, state: dict[str, Any]) -> None:
        cursor = state.get("result_cursor") if isinstance(state, dict) else None
        if cursor is not None and (not isinstance(cursor, list) or len(cursor) != 2):
            raise ValueError("INVALID_RESULT_CURSOR")
        self._result_cursor = (float(cursor[0]), int(cursor[1])) if cursor else None
    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"account_id": self._account_id, "configured_account_id": self._configured_account_id,
                   "current_account_mode": self._current_account_mode, "detected_account_id": self._detected_account_id,
                   "detected_account_ids": sorted(self._detected_account_ids),
                   "account_mismatch": self._account_mismatch, "written": self.written_count,
                   "heartbeats": self.heartbeat_written, "heartbeat_failures": self.heartbeat_failed,
                   "beats_failing_in_a_row": self._beats_failing,
                   "results_acked": self.results_acked, "results_failed": self.results_failed,
                   "identity_incomplete": self.identity_incomplete}
        if self._account_mismatch: return HealthStatus(state=HealthState.UNHEALTHY, message=self.last_write_error or "ACCOUNT_ID_MISMATCH", details=details)
        if self._beats_failing >= _BEAT_FAILURES_BEFORE_FAULT: return HealthStatus(state=HealthState.UNHEALTHY, message=f"heartbeat not reaching bridge for {self._beats_failing} tries", details=details)
        if self.last_write_success is False and not self.heartbeat_written: return HealthStatus(state=HealthState.UNHEALTHY, message=f"last write failed: {self.last_write_error}", details=details)
        if self.last_write_success is False: return HealthStatus(state=HealthState.DEGRADED, message=f"command not written: {self.last_write_error}", details=details)
        if self.heartbeat_written or self.last_write_success: return HealthStatus(state=HealthState.HEALTHY, message=f"bridge writable, written={self.written_count}", details=details)
        return HealthStatus(state=HealthState.DEGRADED, message="no heartbeat yet", details=details)
