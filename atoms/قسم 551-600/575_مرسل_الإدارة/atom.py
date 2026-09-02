from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.3.0"
# v2.3.0 (2026-08-25): a management command that races the first clock
# pulse is HELD, not lost. Measured at boot: 577's replayed MAINTAIN_STOP
# arrived before the first SYS_SECOND, failed OFFICIAL_TIME_UNAVAILABLE
# and was gone forever -- a protective stop dropped by boot ordering.
# The hold is bounded and flushed on the first pulse.
_PENDING_MAX = 64

EVENT_PULSE = "SYS_SECOND"
EVENT_COMMAND = "execution.manage.command"
EVENT_WRITTEN = "execution.manage.written"
EVENT_FAILED = "execution.command.failed"
EVENT_GATE_COMMAND = "execution.gate.command"
# Same halt contract 552 already honours, word for word: emergency.halt carries
# either an account_id (that account only) or scope=SYSTEM (everything). A halt
# naming neither is counted, never widened into a system halt by assumption.
EVENT_HALT = "emergency.halt"
EVENT_RESET = "risk.kill_switch.reset_requested"
REASON_HALT = "OWNER_HALT"
GATE_ID = "575"

ACTION_MODIFY = "MODIFY_SL"
ACTION_PARTIAL = "CLOSE_PARTIAL"
ACTION_CLOSE = "CLOSE"

_ACTIONS = (ACTION_MODIFY, ACTION_PARTIAL, ACTION_CLOSE)

_BUSY_TIMEOUT_MS = 3000
_CONNECT_TIMEOUT_S = 5.0
PROJECT_BUILD_ID = "QUANT_NQ_FULL_212"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS commands ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,"
    "action TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT, volume REAL,"
    "price REAL, stop_loss REAL, take_profit REAL, ticket INTEGER,"
    "trail_dist REAL, trail_step REAL, params_json TEXT, magic INTEGER NOT NULL DEFAULT 0,"
    "account_id TEXT NOT NULL DEFAULT '', project_build_id TEXT,"
    "status TEXT NOT NULL DEFAULT 'PENDING', result TEXT,"
    "created_at REAL NOT NULL, taken_at REAL, done_at REAL)")
_INDEX = "CREATE INDEX IF NOT EXISTS idx_cmd_status ON commands(status, id)"
_INSERT_SQL = (
    "INSERT INTO commands (request_id, action, symbol, side, volume,"
    " ticket, stop_loss, take_profit, params_json, magic, account_id, project_build_id, status, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)")

REASON_NOT_STARTED = "NOT_STARTED"
REASON_DISABLED = "DISABLED"


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_db(configured: str) -> str:
    override = os.environ.get("NQ_BRIDGE_DB", "").strip()
    return override or configured


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=_CONNECT_TIMEOUT_S)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class Atom(AtomBase):
    def __init__(self) -> None:
        self._dropped = 0
        self._context: AtomContext | None = None
        self._running = False
        self._enabled = False
        self._db_path = "nq_brain.db"
        self._counter = 0
        self._seen = 0
        self._written = 0
        self._skipped = 0
        self._failed = 0
        self._last_error = ""
        self._official_time = 0.0
        self._pending_clock: list[dict[str, Any]] = []
        self._magic = 20260801
        self._global_halted = False
        self._halted_accounts: dict[str, str] = {}
        self._halt_blocked = 0
        self._halt_exit_allowed = 0
        self._halt_identity_blocked = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._enabled = bool(context.config["enabled"])
        self._db_path = _resolve_db(str(context.config["db_path"]))
        self._magic = int(context.config.get("magic",20260801))
        context.subscribe(EVENT_COMMAND, self._on_command)
        context.subscribe(EVENT_PULSE, self._on_pulse)
        context.subscribe(EVENT_GATE_COMMAND, self._on_gate_command)
        context.subscribe(EVENT_HALT, self._on_halt)
        context.subscribe(EVENT_RESET, self._on_reset)

    async def _on_halt(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        if account:
            self._halted_accounts[account] = str(payload.get("reason") or "").strip() or "RISK_HALT"
        elif str(payload.get("scope") or "").upper() == "SYSTEM":
            self._global_halted = True
        else:
            self._halt_identity_blocked += 1

    async def _on_reset(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        if account:
            self._halted_accounts.pop(account, None)
        elif str(payload.get("scope") or "").upper() == "SYSTEM":
            self._global_halted = False
        else:
            self._halt_identity_blocked += 1

    def _halt_reason(self, account_id: str) -> str | None:
        if self._global_halted:
            return REASON_HALT
        if account_id in self._halted_accounts:
            return REASON_HALT
        return None

    async def _on_gate_command(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            self._dropped += 1
            return
        if str(payload.get("gate") or "") not in (GATE_ID, "both"):
            self._dropped += 1
            return
        wanted = payload.get("enabled")
        if isinstance(wanted, bool):
            self._enabled = wanted

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        official = payload.get("official_time")
        if isinstance(official, (int, float)) and not isinstance(official, bool):
            self._official_time = float(official)
            if self._pending_clock:
                held, self._pending_clock = self._pending_clock, []
                for command in held:
                    await self._on_command(command)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _insert(self, row: tuple) -> None:
        conn = _connect(self._db_path)
        try:
            conn.execute(_SCHEMA)
            columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(commands)")}
            if "params_json" not in columns:
                conn.execute("ALTER TABLE commands ADD COLUMN params_json TEXT")
            if "magic" not in columns:
                conn.execute("ALTER TABLE commands ADD COLUMN magic INTEGER NOT NULL DEFAULT 0")
            if "account_id" not in columns:
                conn.execute("ALTER TABLE commands ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
            if "project_build_id" not in columns:
                conn.execute("ALTER TABLE commands ADD COLUMN project_build_id TEXT")
            conn.execute(_INDEX)
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM commands WHERE account_id=? AND request_id=?", (row[10], row[0])).fetchone():
                raise sqlite3.IntegrityError("DUPLICATE_REQUEST_ID")
            target = conn.execute("SELECT 1 FROM positions_v2 WHERE account_id=? AND ticket=? AND symbol=? AND magic=?", (row[10], row[5], row[2],row[9])).fetchone()
            if target is None:
                raise sqlite3.IntegrityError("POSITION_OWNERSHIP_MISMATCH")
            conn.execute(_INSERT_SQL, row)
            conn.commit()
        finally:
            conn.close()

    async def _on_command(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        action = str(payload.get("action", ""))
        ticket = _to_int(payload.get("ticket"))
        if action not in _ACTIONS or ticket is None or ticket <= 0:
            return
        self._seen += 1
        if not self._enabled:
            self._skipped += 1
            await self._context.publish(EVENT_FAILED,{**payload,"reason":"MANAGEMENT_GATE_DISABLED"})
            return
        account_id = str(payload.get("account_id") or "").strip()
        if not account_id:
            self._failed += 1
            self._last_error = "MISSING_ACCOUNT_ID"
            return
        # v2.2.0 (2026-08-25): the halt no longer blocks EXITS. Every action
        # this atom sends (CLOSE / CLOSE_PARTIAL / MODIFY_SL) reduces or
        # protects exposure -- blocking them during an emergency halt was a
        # measured safety inversion: the owner halts the system and thereby
        # loses the ability to get OUT of the market. Exits during halt are
        # counted and declared, never refused.
        halted = self._halt_reason(account_id)
        if halted is not None:
            self._halt_exit_allowed += 1
        try:command_magic=int(payload.get("magic"))
        except (TypeError,ValueError):command_magic=0
        if command_magic!=self._magic:
            self._failed+=1;self._last_error="MISSING_OR_FOREIGN_MAGIC"
            await self._context.publish(EVENT_FAILED,{**payload,"reason":self._last_error});return
        symbol = str(payload.get("symbol", ""))
        side = str(payload.get("side", ""))
        volume = _to_float(payload.get("volume"))
        stop_loss = _to_float(payload.get("stop_loss"))
        if (action == ACTION_MODIFY and (stop_loss is None or stop_loss <= 0)) or (action == ACTION_PARTIAL and (volume is None or volume <= 0)):
            self._failed += 1; self._last_error = "INVALID_MANAGEMENT_PARAMETERS"
            await self._context.publish(EVENT_FAILED,{**payload,"reason":self._last_error}); return
        if self._official_time <= 0:
            # v2.3.0: يُحفظ حتى أول نبضة بدل أن يضيع — أمر حماية لا يُرمى
            # لعِلّة ترتيب الإقلاع. تجاوز السقف وحده يُعدّ فشلًا معلَنًا.
            if len(self._pending_clock) < _PENDING_MAX:
                self._pending_clock.append(dict(payload)); return
            self._failed += 1; self._last_error = "OFFICIAL_TIME_UNAVAILABLE"
            await self._context.publish(EVENT_FAILED,{**payload,"reason":self._last_error}); return
        self._counter += 1
        request_id = str(payload.get("request_id") or "mgmt-%s-%d-%d" % (action, ticket, self._counter)).strip()
        metadata = {key: payload.get(key) for key in (
            "extraction_id", "target_amount", "origin", "bias", "score"
        ) if payload.get(key) is not None}
        params_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        row = (request_id, action, symbol, side, volume, ticket, stop_loss,
               None, params_json, command_magic, account_id, PROJECT_BUILD_ID, self._official_time)
        try:
            await asyncio.to_thread(self._insert, row)
            self._written += 1
            self._last_error = ""
            await self._context.publish(EVENT_WRITTEN, {
                "request_id": request_id, "account_id": account_id,
                "action": action, "ticket": ticket, "symbol": symbol, **metadata})
        except sqlite3.Error as exc:
            self._failed += 1
            self._last_error = str(exc)
            self._context.logger.error("575 manage write failed: %s", exc)
            await self._context.publish(EVENT_FAILED,{**payload,"request_id":request_id,"reason":str(exc)})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"enabled": self._enabled, "seen": self._seen, "written": self._written,
                   "skipped": self._skipped, "failed": self._failed,
                   "last_error": self._last_error,
                   "global_halted": self._global_halted,
                   "halted_accounts": dict(self._halted_accounts),
                   "halt_blocked": self._halt_blocked,
                   "halt_exit_allowed": self._halt_exit_allowed,
                   "halt_identity_blocked": self._halt_identity_blocked}
        if self._global_halted or self._halted_accounts:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_HALT,
                                details=details)
        if not self._enabled:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_DISABLED,
                                details=details)
        if self._failed > 0 and self._written == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=self._last_error,
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="written=%d skipped=%d" % (self._written, self._skipped),
                            details=details)
