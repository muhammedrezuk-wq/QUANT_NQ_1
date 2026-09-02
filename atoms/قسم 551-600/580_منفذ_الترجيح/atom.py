"""Multi-level tilt engine -- NQ seal item 22, package TH (paper Q10 + the
approved continuity amendment S52).

Input (S29): the APPROVED decision and its eight outputs exclusively --
`decision.gate.passed` from gate 467. No pre-filter scores ever.

Output: a graduated tilt magnitude published on `tilt.state` with its full
reason (S21/S36/S37 -- no silent tilt). The engine owns no decision, never
flips a side (S30/S31), and never touches a target itself: the coupling of
the tilt into the target equation is an OPEN OWNER DECISION (S33/S34) that
581 is forbidden to apply until ruled.

Rules (S16-S19): the engine starts with NO curves. Every curve is created by
the owner from the dashboard (901 publishes `tilt.rule.command`), stored
transactionally in the engine-owned SQLite store `var/store/tilt_rules.db`,
audited with a unique command_id, and republished on `tilt.rules.state`.

Curves (amendment S52): owner points (threshold -> amount) form a continuous
piecewise-linear curve with decimal precision -- below the first point 0.0,
above the last point the last amount, linear interpolation between
neighbouring points. No slabs, no 0/1 jumps: rise and retreat (S7/S27/S45)
are natural gradients with no sticking.

State is a barrier, not a number (S15/S41): READY allows; NOT_READY (and any
state that is not READY) blocks increase; STALE/INVALID (and ERROR, grouped
with them by the 451 family-blocking law) block everything. No numeric
mitigation is invented -- prevention is the only approved protection.

Unknown is not negative evidence (S42): a field declared in unknown_fields
contributes zero, declared "unknown" -- never a reduction.

Safety caps (S23/S43): the total is clipped to [-TILT_MAX_TOTAL,
+TILT_MAX_TOTAL]. The paper gives NO number, so the cap defaults to 0.0 (no
tilt leaves the engine) until the owner sets it. It is read from this atom's
config only for now; promotion to a governed dial needs a shared/ line by
the coordinator (out of this atom's scope).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.2.0"

EVENT_GATE_PASSED = "decision.gate.passed"
EVENT_RULE_COMMAND = "tilt.rule.command"
EVENT_RULES_STATE = "tilt.rules.state"
EVENT_TILT_STATE = "tilt.state"

ID_ENGINE = "tilt_engine_580"
ID_RULES = "tilt_rules_580"

# The eight decision outputs (Q10 S3), contract names.
FIELD_DIRECTION = "direction"
FIELD_STRENGTH = "strength"
FIELD_CONFIDENCE = "confidence"
FIELD_CURRENT_DEPTH = "current_depth"
FIELD_REQUIRED_DEPTH = "required_depth"
FIELD_WEIGHT = "weight"
FIELD_RATIO = "ratio"
FIELD_STATE = "state"
EIGHT_FIELDS = (FIELD_DIRECTION, FIELD_STRENGTH, FIELD_CONFIDENCE,
                FIELD_CURRENT_DEPTH, FIELD_REQUIRED_DEPTH, FIELD_WEIGHT,
                FIELD_RATIO, FIELD_STATE)

# Q10 S13/S15 (same exclusion as gate 901): state is a barrier and weight is
# a contribution factor -- neither is a ladder, so neither may carry a curve.
CURVE_FIELDS = (FIELD_DIRECTION, FIELD_STRENGTH, FIELD_CONFIDENCE,
                FIELD_CURRENT_DEPTH, FIELD_REQUIRED_DEPTH, FIELD_RATIO)

# Curve sides (dashboard contract): "up" applies while the field value is
# positive (x = value), "down" while it is negative (x = |value|, thresholds
# are magnitudes), "abs" always (x = |value|). S25: the directional field can
# carry an independent up and down ladder; S26: the middle zone (value 0) is
# never forced onto either side.
SIDE_UP = "up"
SIDE_DOWN = "down"
SIDE_ABS = "abs"
SIDES = (SIDE_UP, SIDE_DOWN, SIDE_ABS)

# Structural bound shared with gate 901 (TILT_MAX_POINTS): how many points
# one curve may carry. Not a trading number.
MAX_POINTS = 12

# Wire keys of the eight on the flat gate payload -- the same sealed mapping
# 455 uses (contract field -> the key 453 actually publishes it under; the
# word lives in "direction", the +-100 number in "direction_value"). Inside
# a sealed "unified" block (166) the contract names themselves are the keys.
FIELD_SOURCES = {
    FIELD_DIRECTION: "direction_value",
    FIELD_STRENGTH: "strength_value",
    FIELD_CONFIDENCE: "confidence_value",
    FIELD_CURRENT_DEPTH: "current_depth",
    FIELD_REQUIRED_DEPTH: "required_depth",
    FIELD_WEIGHT: "weight",
    FIELD_RATIO: "ratio",
}
STATE_SOURCE = "aggregate_state"
UNKNOWN_LISTS = ("unknown_fields", "depth_unknown_fields")

# Identity passthrough: union of the six-field decision identity as published
# by 466 (period_start) and 467 (cycle_id). Absent stays None, never invented.
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "timeframe",
                   "period_start", "cycle_id", "decision_id")

GATE_STATE_PASSED = "PASSED"

# State barrier (Q10 S15/S41). ERROR is grouped with STALE/INVALID by the
# existing 451 family-blocking law -- project law, not an invention here.
STATE_READY = "READY"
BLOCK_ALL_STATES = ("STALE", "INVALID", "ERROR")
RULING_ALLOW = "ALLOW"
RULING_BLOCK_INCREASE = "BLOCK_INCREASE"
RULING_BLOCK_ALL = "BLOCK_ALL"

# Contribution notes (S21/S36/S37: every tilt fully explained).
NOTE_INTERPOLATED = "interpolated"
NOTE_BELOW_FIRST = "below_first_point"
NOTE_BEYOND_LAST = "beyond_last_point"
NOTE_UNKNOWN = "unknown"
NOTE_NO_CURVE = "no_curve"
NOTE_DISABLED = "disabled"
NOTE_NO_POINTS = "no_points"
NOTE_MIDDLE_ZONE = "middle_zone"
NOTE_SIDE_INACTIVE = "side_inactive"
NOTE_MULTIPLE_CURVES = "multiple_curves"
NOTE_BARRIER = "barrier"
NOTE_NOT_CURVABLE = "not_curvable"

REJECT_NOT_DICT = "COMMAND_NOT_A_DICT"
REJECT_FIELD = "FIELD_NOT_CURVABLE_OR_UNKNOWN"
REJECT_SIDE = "SIDE_INVALID"
REJECT_POINTS = "POINTS_INVALID"
REJECT_ENABLED = "ENABLED_NOT_BOOL"
REJECT_OPERATOR = "OPERATOR_REQUIRED"
REJECT_COMMAND_ID = "COMMAND_ID_REQUIRED"
REJECT_REQUESTED_AT = "COMMAND_REQUESTED_AT_INVALID"
REJECT_STORE = "STORE_WRITE_FAILED"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_GATE = "NO_GATE_DECISION_YET"

RESTORE_JOURNAL = "journal"
RESTORE_MEMORY = "memory"

_DB_TIMEOUT_S = 10.0
_BUSY_TIMEOUT_MS = 10000
_DP = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tilt_rules (
    field       TEXT NOT NULL,
    side        TEXT NOT NULL CHECK(side IN ('up','down','abs')),
    points_json TEXT NOT NULL,
    enabled     INTEGER NOT NULL,
    version     INTEGER NOT NULL,
    updated_at  REAL NOT NULL,
    updated_by  TEXT NOT NULL,
    PRIMARY KEY(field, side)
);
CREATE TABLE IF NOT EXISTS tilt_rules_audit (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    field      TEXT NOT NULL,
    side       TEXT NOT NULL,
    old_json   TEXT NOT NULL,
    new_json   TEXT NOT NULL,
    version    INTEGER NOT NULL,
    changed_at REAL NOT NULL,
    changed_by TEXT NOT NULL,
    command_id TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tilt_rules_command
ON tilt_rules_audit(command_id, field, side);
CREATE TABLE IF NOT EXISTS tilt_state_journal (
    symbol      TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    field       TEXT NOT NULL,
    value,
    tilt        REAL NOT NULL,
    total       REAL NOT NULL,
    changed_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tilt_journal_symbol
ON tilt_state_journal(symbol, changed_at);
"""










import rules_store
from ruling_math import _finite, _valid_points, interpolate, _contract_view, _declared_unknown, _read_number, _read_state, _side_applies


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._db_path = Path()
        self._tilt_max_total = 0.0
        self._rules: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_tilt: dict[str, dict[str, Any]] = {}
        self._restored_rules = 0
        self._restored_symbols = 0
        self._corrupt_rules = 0
        self._gate_seen = 0
        self._ignored_not_passed = 0
        self._tilt_published = 0
        self._journal_writes = 0
        self._commands_applied = 0
        self._commands_rejected = 0
        self._commands_duplicate = 0
        self._last_reject = ""
        self._db_error = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        # S23/S43 -- the paper gives NO number: 0.0 (no tilt leaves the
        # engine) until the owner sets it. Config-only for now; promotion to
        # a governed dial needs a shared/ line by the coordinator.
        self._tilt_max_total = max(0.0, float(cfg.get("tilt_max_total", 0.0)))
        raw = Path(str(cfg.get("db_path", "var/store/tilt_rules.db")))
        self._db_path = raw if raw.is_absolute() else (
            Path(__file__).resolve().parents[2] / raw)
        if self._ensure_store():
            self._load_rules()
            self._load_journal_tail()
        context.subscribe(EVENT_GATE_PASSED, self._on_gate_passed)
        context.subscribe(EVENT_RULE_COMMAND, self._on_rule_command)

    async def start(self) -> None:
        self._running = True
        # S39: after boot the engine republishes what it restored -- rules
        # and the last tilt per symbol -- so downstream sees no jump.
        await self._publish_rules_state(restored=True)
        for symbol in sorted(self._last_tilt):
            out = dict(self._last_tilt[symbol])
            out["restored"] = True
            await self._emit(EVENT_TILT_STATE, out)

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # -- store ------------------------------------------------------------

    def _reject(self, reason: str, payload: Any) -> None:
        self._commands_rejected += 1
        self._last_reject = reason
        if self._context is not None:
            self._context.logger.warning(
                "tilt.rule.command rejected: %s payload=%r" % (reason, payload))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), timeout=_DB_TIMEOUT_S)
        connection.row_factory = sqlite3.Row
        connection.isolation_level = None
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _ensure_store(self) -> bool:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                connection.executescript(_SCHEMA)
            finally:
                connection.close()
            self._db_error = ""
            return True
        except (sqlite3.Error, OSError) as exc:
            self._db_error = "STORE_INIT_FAILED:%s" % exc
            return False

    def _load_rules(self, *args, **kwargs):
        return rules_store._load_rules(self, *args, **kwargs)

    def _load_journal_tail(self, *args, **kwargs):
        return rules_store._load_journal_tail(self, *args, **kwargs)

    def _journal(self, *args, **kwargs):
        return rules_store._journal(self, *args, **kwargs)

    async def _on_rule_command(self, payload: dict[str, Any]) -> None:
        import command_handlers
        await command_handlers._on_rule_command(self, payload)

    async def _publish_rules_state(self, restored: bool) -> None:
        rules = []
        for key in sorted(self._rules):
            rule = self._rules[key]
            rules.append({"field": rule["field"], "side": rule["side"],
                          "points": rule["points"],
                          "enabled": rule["enabled"],
                          "version": rule["version"],
                          "updated_at": rule["updated_at"],
                          "updated_by": rule["updated_by"]})
        await self._emit(EVENT_RULES_STATE, {
            "id": ID_RULES, "atom_id": "580", "rules": rules,
            "count": len(rules), "restored": restored})

    # -- the approved decision --------------------------------------------

    def _field_rules(self, field: str) -> list[dict[str, Any]]:
        return [self._rules[(field, side)] for side in SIDES
                if (field, side) in self._rules]

    def _contribution(self, field: str, value: float | None) -> dict[str, Any]:
        rules = self._field_rules(field)
        entry: dict[str, Any] = {"value": value, "tilt": 0.0,
                                 "curve_active": [], "note": NOTE_NO_CURVE,
                                 "curves": {}}
        if value is None:
            # S42: unknown is not negative evidence -- zero, declared.
            entry["note"] = NOTE_UNKNOWN
            return entry
        if not rules:
            return entry
        fired: list[tuple[str, float, list[list[float]], str]] = []
        for rule in rules:
            side = str(rule["side"])
            if not rule["enabled"]:
                entry["curves"][side] = {"tilt": 0.0, "curve_active": [],
                                         "note": NOTE_DISABLED}
                continue
            if rule["points"] is None:
                entry["curves"][side] = {"tilt": 0.0, "curve_active": [],
                                         "note": NOTE_NO_POINTS}
                continue
            if not _side_applies(side, value):
                note = (NOTE_MIDDLE_ZONE if value == 0.0 and side in
                        (SIDE_UP, SIDE_DOWN) else NOTE_SIDE_INACTIVE)
                entry["curves"][side] = {"tilt": 0.0, "curve_active": [],
                                         "note": note}
                continue
            x = value if side == SIDE_UP else abs(value)
            tilt, active, note = interpolate(rule["points"], x)
            entry["curves"][side] = {"tilt": round(tilt, _DP),
                                     "curve_active": active, "note": note}
            fired.append((side, tilt, active, note))
        if fired:
            entry["tilt"] = round(sum(item[1] for item in fired), _DP)
            if len(fired) == 1:
                entry["curve_active"] = fired[0][2]
                entry["note"] = fired[0][3]
            else:
                entry["note"] = NOTE_MULTIPLE_CURVES
        elif entry["curves"]:
            notes = {info["note"] for info in entry["curves"].values()}
            entry["note"] = (NOTE_MIDDLE_ZONE if NOTE_MIDDLE_ZONE in notes
                             else notes.pop() if len(notes) == 1
                             else NOTE_SIDE_INACTIVE)
        return entry

    async def _on_gate_passed(self, payload: dict[str, Any]) -> None:
        import gate_runner
        await gate_runner._on_gate_passed(self, payload)

    async def _emit(self, event: str, body: dict[str, Any]) -> None:
        if self._context is not None:
            await self._context.publish(event, body)

    # -- health ------------------------------------------------------------

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY,
                                message=REASON_NOT_STARTED)
        details = {
            "rules": len(self._rules),
            "rules_enabled": sum(1 for rule in self._rules.values()
                                 if rule["enabled"]),
            "corrupt_rules": self._corrupt_rules,
            "restored_rules": self._restored_rules,
            "restored_symbols": self._restored_symbols,
            "gate_seen": self._gate_seen,
            "ignored_not_passed": self._ignored_not_passed,
            "tilt_published": self._tilt_published,
            "journal_writes": self._journal_writes,
            "symbols": len(self._last_tilt),
            "commands_applied": self._commands_applied,
            "commands_rejected": self._commands_rejected,
            "commands_duplicate": self._commands_duplicate,
            "last_reject": self._last_reject,
            "tilt_max_total": self._tilt_max_total,
            "db_error": self._db_error}
        if self._db_error:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=self._db_error, details=details)
        if self._gate_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_GATE, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="tilt=%d rules=%d" % (self._tilt_published,
                                          len(self._rules)),
            details=details)
