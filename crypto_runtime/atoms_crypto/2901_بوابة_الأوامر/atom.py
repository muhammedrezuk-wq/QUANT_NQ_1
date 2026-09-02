from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.decision_dials import DIALS as _DECISION_DIALS
from shared.live_analysis import DEFAULT_WEIGHTS as _ANALYSIS_ANALYZERS
from shared.live_analysis import TUNABLE_SETTINGS as _ANALYSIS_TUNABLE_TUPLE
from shared.parameter_registry import DECLARED as _DECLARED_PARAMETERS
from shared.parameter_registry import SOURCE_OWNER, ParameterRegistry

ATOM_VERSION = "2.6.0"

# Owner stamp 2026-08-21: the analyser dial list is owned by the calibration
# store, not copied here. A hardcoded triple was silently rejecting the three
# threshold dials the owner approved (strength / staleness / neutral band),
# and any future dial would have been rejected the same silent way.
_ANALYSIS_TUNABLE = frozenset(_ANALYSIS_TUNABLE_TUPLE)
#: The fifteen analyser ids a whole-camp weight table must carry, no more.
_ANALYSIS_WEIGHT_KEYS = frozenset(_ANALYSIS_ANALYZERS)
#: Rounding slack on a weight table total. Not a trading value: the store
#: rounds every weight to four decimals, so a table the owner meant as 100
#: can land a hair off. Anything wider than this is a real mistake, refused.
_WEIGHT_TOTAL_TOLERANCE = 0.01

EVENT_PULSE = "SYS_SECOND"
EVENT_HALT_REQUEST = "risk.halt.requested"
ORIGIN = "901"
EVENT_RESET = "risk.release.requested"
EVENT_ACTIVATE = "perpetual.asset.activate"
EVENT_DEACTIVATE = "perpetual.asset.deactivate"
EVENT_GATE_COMMAND = "execution.gate.command"
EVENT_ASSET_COMMAND = "risk.asset.command"
EVENT_DIAL_COMMAND = "dial.command"
EVENT_RECONCILE = "execution.reconcile.requested"
EVENT_BUDGET = "risk.asset_budget.state"
EVENT_STATE = "system.commands.state"
EVENT_ANALYSIS_SETTINGS = "analysis.settings.command"
EVENT_DECISION_SETTINGS = "decision.settings.command"
EVENT_PARAMETER_APPROVE = "parameter.approve.command"
EVENT_PARAMETER_STATE = "parameter.approved.state"
EVENT_TILT_RULE = "tilt.rule.command"

ACTION_HALT = "halt"
ACTION_RESET = "kill_switch_reset"
ACTION_ACTIVATE = "activate_asset"
ACTION_DEACTIVATE = "deactivate_asset"
ACTION_CONTROL = "asset_control"
ACTION_GATE = "execution_gate"
ACTION_ANALYSIS_SETTINGS = "analysis_setting"
ACTION_DECISION_SETTINGS = "decision_setting"
ACTION_PARAMETER_APPROVE = "parameter_approve"
ACTION_TILT_RULE = "tilt_rule"
# v2.6.0 (nq seal 2026-08-25): the adaptation kill switch had a subscriber
# (860) and ZERO publishers -- a one-way door: once tripped, only a restart
# could bring adaptation back. The owner's command bridge now carries it.
ACTION_ADAPTATION = "adaptation_switch"
EVENT_ADAPTATION_COMMAND = "adaptation.kill_switch.command"
GATE_ATOMS = {"552", "575", "both"}
# Tilt engine (580) rule contract -- paper Q10 S18-S21 (package TH, item TH3).
# state and weight are excluded on purpose: state is a barrier and weight is
# a contribution factor, neither is a ladder of threshold points.
TILT_FIELDS = {"direction", "strength", "confidence", "current_depth",
               "required_depth", "ratio"}
TILT_SIDES = {"up", "down", "abs"}
TILT_MAX_POINTS = 12
TILT_PAYLOAD_KEYS = {"field", "side", "points", "enabled"}
CONTROL_COMMANDS = {"PAUSE","RESUME","FREEZE","UNFREEZE","CALIBRATE","FORCE_RECONCILE","SET_BUDGET","SET_MAX_PER_SYMBOL"}
ACTIONS = {ACTION_HALT: EVENT_HALT_REQUEST, ACTION_RESET: EVENT_RESET,
           ACTION_ACTIVATE: EVENT_ACTIVATE, ACTION_DEACTIVATE: EVENT_DEACTIVATE,
           ACTION_GATE: EVENT_GATE_COMMAND,
           ACTION_CONTROL: EVENT_ASSET_COMMAND,
           ACTION_ANALYSIS_SETTINGS: EVENT_ANALYSIS_SETTINGS,
           ACTION_DECISION_SETTINGS: EVENT_DECISION_SETTINGS,
           ACTION_PARAMETER_APPROVE: EVENT_PARAMETER_APPROVE,
           ACTION_TILT_RULE: EVENT_TILT_RULE,
           ACTION_ADAPTATION: EVENT_ADAPTATION_COMMAND}

STATUS_PENDING = "PENDING"
STATUS_DONE = "DONE"
STATUS_EXPIRED = "EXPIRED"
STATUS_REJECTED = "REJECTED"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_COMMANDS = "NO_COMMANDS_YET"
REASON_UNREADABLE = "BRIDGE_UNREADABLE"
REASON_OWNER = "OWNER_COMMAND"

_BUSY_TIMEOUT_MS = 3000

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS commands ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "action TEXT NOT NULL, "
    "operator TEXT NOT NULL, "
    "requested_at REAL NOT NULL, "
    "status TEXT NOT NULL DEFAULT 'PENDING', "
    "executed_at REAL, payload_json TEXT)")


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _finite_number(value: Any) -> float | None:
    # bool is an int subclass -- a curve point of true/false must not pass
    # as 1.0/0.0, so booleans are rejected before the numeric check.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _valid_tilt_points(points: Any) -> list[list[float]] | None:
    # Curve points: up to TILT_MAX_POINTS pairs [threshold, amount], every
    # number finite, thresholds strictly ascending. An empty list is legal
    # (the owner clearing a curve). Returns the normalized float pairs, or
    # None when the shape is invalid.
    if not isinstance(points, list) or len(points) > TILT_MAX_POINTS:
        return None
    normalized: list[list[float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        threshold = _finite_number(point[0])
        amount = _finite_number(point[1])
        if threshold is None or amount is None:
            return None
        normalized.append([threshold, amount])
    thresholds = [pair[0] for pair in normalized]
    if any(b <= a for a, b in zip(thresholds, thresholds[1:])):
        return None
    return normalized


def _normal_path(value: str) -> Path:
    text = value
    if os.sep != "\\":
        text = text.replace("\\", os.sep)
    return Path(text)


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._db_path = Path()
        self._max_age_s = 0.0
        self._batch_limit = 0
        self._conn: sqlite3.Connection | None = None
        self._last_error = ""
        self._seen = 0
        # One source: counters are derived from ACTIONS itself. This used to be
        # a hand-written copy that drifted by exactly one action (execution_gate),
        # so counting blew up AFTER the command was published and BEFORE the row
        # was stamped DONE -- leaving it PENDING and re-dispatched every pulse.
        self._executed = {action: 0 for action in ACTIONS}
        self._expired = 0
        self._rejected = 0
        self._state_dirty = True

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        raw = _normal_path(str(cfg["db_path"]))
        self._db_path = raw if raw.is_absolute() else Path.cwd() / raw
        self._max_age_s = float(cfg["max_age_s"])
        self._batch_limit = int(cfg["batch_limit"])
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    async def shutdown(self) -> None:
        await self.stop()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
        conn.execute(_SCHEMA)
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(commands)")}
        if "payload_json" not in columns:
            conn.execute("ALTER TABLE commands ADD COLUMN payload_json TEXT")
        conn.commit()
        conn.row_factory = sqlite3.Row
        self._conn = conn
        return conn

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = _to_float(payload.get("official_time"))
        if now is None:
            return
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, action, operator, requested_at, payload_json FROM commands "
                "WHERE status = ? ORDER BY id LIMIT ?",
                (STATUS_PENDING, self._batch_limit)).fetchall()
            for row in rows:
                await self._handle(conn, row, now)
            if rows:
                conn.commit()
                self._state_dirty = True
        except sqlite3.Error as exc:
            self._last_error = str(exc)
            self._conn = None
            return
        self._last_error = ""
        if self._state_dirty:
            self._state_dirty = False
            await self._publish_state()

    def _payload(self, row: sqlite3.Row) -> dict[str, Any] | None:
        raw = row["payload_json"]
        if raw in (None, ""):
            return {}
        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    async def _handle(self, conn: sqlite3.Connection, row: sqlite3.Row,
                      now: float) -> None:
        if self._context is None:
            return
        self._seen += 1
        action = str(row["action"])
        event = ACTIONS.get(action)
        requested = _to_float(row["requested_at"]) or 0.0
        data = self._payload(row)
        valid_payload = True
        if action == ACTION_ACTIVATE:
            account_id = str((data or {}).get("account_id") or "").strip()
            symbol = str((data or {}).get("symbol") or "").strip()
            budget = _to_float((data or {}).get("budget"))
            valid_payload = bool(account_id and symbol and budget is not None and budget > 0)
        if action == ACTION_DEACTIVATE:
            valid_payload = bool(str((data or {}).get("account_id") or "").strip()
                                 and str((data or {}).get("symbol") or "").strip())
        if action == ACTION_GATE:
            gate = str((data or {}).get("gate") or "").strip()
            valid_payload = (gate in GATE_ATOMS
                             and isinstance((data or {}).get("enabled"), bool))
        if action == ACTION_ANALYSIS_SETTINGS:
            item = data or {}
            settings = item.get("settings")
            table = item.get("weights")
            scope_ok = bool(str(item.get("account_id") or "").strip()
                            and str(item.get("symbol") or "").strip())
            if isinstance(table, dict) and table:
                # Owner stamp 2026-08-21: a whole-camp weight table in one
                # command. Writing fifteen weights one by one makes each
                # command redistribute the one before it, so the table never
                # lands as written. The table travels together and the store
                # refuses it unless the fifteen are complete and sum to 100.
                valid_payload = scope_ok and set(table) == _ANALYSIS_WEIGHT_KEYS
                total = 0.0
                if valid_payload:
                    for value in table.values():
                        number = _to_float(value)
                        if number is None or not 0 <= number <= 100:
                            valid_payload = False
                            break
                        total += number
                    if valid_payload and abs(total - 100.0) > _WEIGHT_TOTAL_TOLERANCE:
                        valid_payload = False
            else:
                valid_payload = bool(scope_ok
                                     and str(item.get("analyzer_id") or "").strip()
                                     and isinstance(settings, dict) and settings
                                     and not (set(settings) - _ANALYSIS_TUNABLE))
                if valid_payload:
                    for value in settings.values():
                        number = _to_float(value)
                        if number is None or not 0 <= number <= 100:
                            valid_payload = False
                        break
        if action == ACTION_DECISION_SETTINGS:
            item = data or {}
            dial_name = str(item.get("name") or "")
            dial_spec = _DECISION_DIALS.get(dial_name)
            dial_value = _to_float(item.get("value"))
            valid_payload = bool(
                dial_spec is not None and dial_value is not None
                and dial_spec["bounds"][0] <= dial_value <= dial_spec["bounds"][1])
        if action == ACTION_PARAMETER_APPROVE:
            # Declared parameters only (shared.parameter_registry.DECLARED).
            # Decision dial names keep their own decision_setting path and
            # are rejected here. Operator identity is required because the
            # registry refuses an approval without approved_by.
            item = data or {}
            parameter_name = str(item.get("name") or "")
            parameter_value = _to_float(item.get("value"))
            valid_payload = bool(parameter_name in _DECLARED_PARAMETERS
                                 and parameter_value is not None
                                 and str(row["operator"]).strip())
        if action == ACTION_TILT_RULE:
            # Tilt curve rule for engine 580 (Q10 S18-S21). 901 validates and
            # publishes only -- it never applies: 580 is the sole owner of
            # the tilt rules store. field is one of the six curve-able
            # decision outputs (state/weight rejected: barrier and factor,
            # not ladders), side one of up/down/abs, points a strictly
            # ascending list of [threshold, amount] finite pairs (max 12),
            # enabled an explicit boolean, and no foreign keys ride along.
            item = data or {}
            tilt_points = _valid_tilt_points(item.get("points"))
            valid_payload = bool(
                str(item.get("field")) in TILT_FIELDS
                and str(item.get("side")) in TILT_SIDES
                and isinstance(item.get("enabled"), bool)
                and tilt_points is not None
                and not (set(item) - TILT_PAYLOAD_KEYS))
            if valid_payload and tilt_points is not None:
                item["points"] = tilt_points  # normalized float pairs
        if action == ACTION_ADAPTATION:
            # 860's contract: owner identity + an explicit ON/OFF, nothing
            # widened by default. The operator on the command row is the
            # owner identity unless the payload names one.
            switch = str((data or {}).get("action") or "").upper()
            valid_payload = bool(switch in ("ON", "OFF")
                                 and str(row["operator"]).strip())
            if valid_payload and data is not None:
                data["action"] = switch
                data.setdefault("owner", str(row["operator"]))
        if action == ACTION_CONTROL:
            command = str((data or {}).get("command") or "").upper()
            valid_payload = bool((data or {}).get("account_id") and (data or {}).get("symbol") and command in CONTROL_COMMANDS)
            if command == "CALIBRATE": valid_payload = valid_payload and _to_float((data or {}).get("dial")) is not None
            if command == "SET_BUDGET": valid_payload = valid_payload and (_to_float((data or {}).get("risk_budget")) or 0) > 0
            if command == "SET_MAX_PER_SYMBOL": valid_payload = valid_payload and (_to_float((data or {}).get("max_per_symbol")) or 0) >= 1
        if event is None or data is None or not valid_payload:
            status = STATUS_REJECTED
            self._rejected += 1
        elif now - requested > self._max_age_s:
            status = STATUS_EXPIRED
            self._expired += 1
        else:
            body = dict(data)
            body.update({"operator": str(row["operator"]), "origin": ORIGIN,
                         "reason": REASON_OWNER, "command_id": int(row["id"]),
                         "command_requested_at": requested})
            await self._context.publish(event, body)
            if action == ACTION_CONTROL:
                command = str(data.get("command") or "").upper()
                if command == "CALIBRATE": await self._context.publish(EVENT_DIAL_COMMAND, body)
                elif command == "FORCE_RECONCILE": await self._context.publish(EVENT_RECONCILE, body)
                elif command == "SET_BUDGET": await self._context.publish(EVENT_BUDGET, {**body, "risk_budget": data.get("risk_budget")})
            if action == ACTION_PARAMETER_APPROVE:
                await self._apply_parameter_approval(body, now)
            status = STATUS_DONE
            self._executed[action] += 1
        conn.execute(
            "UPDATE commands SET status = ?, executed_at = ? WHERE id = ?",
            (status, now, int(row["id"])))

    async def _apply_parameter_approval(self, body: dict[str, Any],
                                        now: float) -> None:
        # 901 owns the command bridge, so it applies the owner's approval
        # itself: registry write is idempotent per command_id, then the
        # approved state is published for the dashboard. A sqlite error in
        # the registry propagates to the pulse loop, so the command stays
        # PENDING and is retried on the next pulse.
        if self._context is None:
            return
        try:
            approved = ParameterRegistry().approve(
                str(body["name"]), value=float(body["value"]),
                source=SOURCE_OWNER, approved_by=str(body["operator"]),
                command_id=str(body["command_id"]), approved_at=now)
        except ValueError as exc:
            # Unreachable through the payload validation above; recorded
            # instead of crashing the pulse loop if it ever happens.
            self._last_error = str(exc)
            return
        await self._context.publish(EVENT_PARAMETER_STATE, {
            "name": str(approved["name"]), "value": float(approved["value"]),
            "version": int(approved["version"])})

    async def _publish_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_STATE, {
            "id": "command_gateway", "seen": self._seen,
            "executed_halt": self._executed[ACTION_HALT],
            "executed_reset": self._executed[ACTION_RESET],
            "executed_activate": self._executed[ACTION_ACTIVATE],
            "expired": self._expired, "rejected": self._rejected,
            "last_error": self._last_error})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY,
                                message=REASON_NOT_STARTED)
        details = {
            "seen": self._seen,
            "executed_halt": self._executed[ACTION_HALT],
            "executed_reset": self._executed[ACTION_RESET],
            "executed_activate": self._executed[ACTION_ACTIVATE],
            "expired": self._expired, "rejected": self._rejected,
            "db": str(self._db_path)}
        if self._last_error:
            details["last_error"] = self._last_error
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_UNREADABLE, details=details)
        if self._seen == 0:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_DASHBOARD_COMMAND | halt=0 reset=0 activate=0",
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="halt=%d reset=%d activate=%d expired=%d rejected=%d" % (
                self._executed[ACTION_HALT], self._executed[ACTION_RESET],
                self._executed[ACTION_ACTIVATE], self._expired, self._rejected),
            details=details)
