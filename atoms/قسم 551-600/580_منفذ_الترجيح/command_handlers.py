from __future__ import annotations

from typing import Any

import json
import sqlite3

import rules_store
from ruling_math import (_valid_points, _finite, interpolate, _contract_view, _declared_unknown, _read_number, _read_state, _side_applies, FIELD_DIRECTION, FIELD_STRENGTH, FIELD_CONFIDENCE, ATOM_VERSION, BLOCK_ALL_STATES, EIGHT_FIELDS, EVENT_GATE_PASSED, EVENT_RULES_STATE, EVENT_RULE_COMMAND, EVENT_TILT_STATE, FIELD_CURRENT_DEPTH, FIELD_RATIO, FIELD_REQUIRED_DEPTH, FIELD_SOURCES, FIELD_STATE, FIELD_WEIGHT, GATE_STATE_PASSED, IDENTITY_FIELDS, ID_ENGINE, MAX_POINTS, NOTE_BARRIER, NOTE_BELOW_FIRST, NOTE_BEYOND_LAST, NOTE_DISABLED, NOTE_INTERPOLATED, NOTE_MIDDLE_ZONE, NOTE_MULTIPLE_CURVES, NOTE_NOT_CURVABLE, NOTE_NO_CURVE, NOTE_NO_POINTS, NOTE_SIDE_INACTIVE, NOTE_UNKNOWN, REASON_NOT_STARTED, REASON_NO_GATE, RESTORE_JOURNAL, RESTORE_MEMORY, RULING_ALLOW, RULING_BLOCK_ALL, RULING_BLOCK_INCREASE, SIDE_ABS, SIDE_DOWN, SIDE_UP, STATE_NOT_READY, STATE_READY, STATE_SOURCE, UNKNOWN_LISTS, _BUSY_TIMEOUT_MS, _DB_TIMEOUT_S, _DP, _SCHEMA)
SIDES = (SIDE_UP, SIDE_DOWN, SIDE_ABS)
CURVE_FIELDS = (FIELD_DIRECTION, FIELD_STRENGTH, FIELD_CONFIDENCE,
                FIELD_CURRENT_DEPTH, FIELD_REQUIRED_DEPTH, FIELD_RATIO)
REJECT_NOT_DICT = "COMMAND_NOT_A_DICT"
REJECT_FIELD = "FIELD_NOT_CURVABLE_OR_UNKNOWN"
REJECT_SIDE = "SIDE_INVALID"
REJECT_POINTS = "POINTS_INVALID"
REJECT_ENABLED = "ENABLED_NOT_BOOL"
REJECT_OPERATOR = "OPERATOR_REQUIRED"
REJECT_COMMAND_ID = "COMMAND_ID_REQUIRED"
REJECT_REQUESTED_AT = "COMMAND_REQUESTED_AT_INVALID"
REJECT_STORE = "STORE_WRITE_FAILED"


# Campaign 450-901 batch B: the rule command handler extracted verbatim.

async def _on_rule_command(atom, payload: dict[str, Any]) -> None:
    if not atom._running or atom._context is None:
        return
    if not isinstance(payload, dict):
        atom._reject(REJECT_NOT_DICT, payload)
        return
    field = str(payload.get("field") or "")
    if field not in CURVE_FIELDS:
        # Covers state and weight explicitly (S13/S15: the barrier and
        # the contribution factor are not ladders) plus unknown names.
        atom._reject(REJECT_FIELD, payload)
        return
    side = str(payload.get("side") or "")
    if side not in SIDES:
        atom._reject(REJECT_SIDE, payload)
        return
    points = _valid_points(payload.get("points"))
    if points is None:
        atom._reject(REJECT_POINTS, payload)
        return
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        atom._reject(REJECT_ENABLED, payload)
        return
    operator = str(payload.get("operator") or "").strip()
    if not operator:
        atom._reject(REJECT_OPERATOR, payload)
        return
    raw_id = payload.get("command_id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
        atom._reject(REJECT_COMMAND_ID, payload)
        return
    command_id = str(raw_id).strip()
    if not command_id:
        atom._reject(REJECT_COMMAND_ID, payload)
        return
    requested_at = _finite(payload.get("command_requested_at"))
    if requested_at is None:
        atom._reject(REJECT_REQUESTED_AT, payload)
        return
    try:
        connection = atom._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT audit_id FROM tilt_rules_audit"
                " WHERE command_id=? AND field=? AND side=?",
                (command_id, field, side)).fetchone()
            if duplicate is not None:
                # Idempotent per command_id (project audit law): the
                # first application stands; a replay changes nothing.
                connection.execute("ROLLBACK")
                atom._commands_duplicate += 1
                await atom._publish_rules_state(restored=False)
                return
            row = connection.execute(
                "SELECT points_json, enabled, version, updated_at,"
                " updated_by FROM tilt_rules WHERE field=? AND side=?",
                (field, side)).fetchone()
            old: dict[str, Any] = {}
            version = 1
            if row is not None:
                version = int(row["version"]) + 1
                old = {"field": field, "side": side,
                       "points_json": str(row["points_json"]),
                       "enabled": bool(row["enabled"]),
                       "version": int(row["version"]),
                       "updated_at": float(row["updated_at"]),
                       "updated_by": str(row["updated_by"])}
            new = {"field": field, "side": side, "points": points,
                   "enabled": enabled, "version": version,
                   "updated_at": requested_at, "updated_by": operator}
            connection.execute(
                "INSERT INTO tilt_rules(field, side, points_json,"
                " enabled, version, updated_at, updated_by)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(field, side) DO UPDATE SET"
                " points_json=excluded.points_json,"
                " enabled=excluded.enabled, version=excluded.version,"
                " updated_at=excluded.updated_at,"
                " updated_by=excluded.updated_by",
                (field, side, json.dumps(points), int(enabled), version,
                 requested_at, operator))
            connection.execute(
                "INSERT INTO tilt_rules_audit(field, side, old_json,"
                " new_json, version, changed_at, changed_by, command_id)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (field, side, json.dumps(old, sort_keys=True),
                 json.dumps(new, sort_keys=True), version, requested_at,
                 operator, command_id))
            connection.execute("COMMIT")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        # Store-first law (S39): a rule that is not stored does not
        # exist -- memory is never updated ahead of the store.
        atom._db_error = "RULE_STORE_FAILED:%s" % exc
        atom._reject(REJECT_STORE, {"field": field, "side": side})
        return
    atom._db_error = ""
    atom._rules[(field, side)] = {
        "field": field, "side": side, "points": points,
        "enabled": enabled, "version": version,
        "updated_at": requested_at, "updated_by": operator}
    atom._commands_applied += 1
    await atom._publish_rules_state(restored=False)