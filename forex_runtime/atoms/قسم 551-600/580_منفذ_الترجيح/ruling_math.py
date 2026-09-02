from __future__ import annotations

from typing import Any

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
STATE_NOT_READY = "NOT_READY"
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


def _finite(value: Any) -> float | None:
    """Strict finite number (bool rejected) -- the 901 command number rule."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _valid_points(points: Any) -> list[list[float]] | None:
    """Mirror of 901's curve validation: at most MAX_POINTS pairs
    [threshold, amount], every number finite, thresholds strictly ascending.
    An empty list is legal (the owner clearing a curve). Returns normalized
    float pairs, or None when the shape is invalid."""
    if not isinstance(points, list) or len(points) > MAX_POINTS:
        return None
    normalized: list[list[float]] = []
    previous: float | None = None
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        threshold = _finite(point[0])
        amount = _finite(point[1])
        if threshold is None or amount is None:
            return None
        if previous is not None and threshold <= previous:
            return None
        previous = threshold
        normalized.append([threshold, amount])
    return normalized


def interpolate(points: list[list[float]],
                x: float) -> tuple[float, list[list[float]], str]:
    """The sealed continuity rule (amendment S52): below the first point 0.0,
    above the last point the last amount, linear interpolation between the
    two neighbouring points -- decimal precision, no slabs, no 0/1 jumps.
    Returns (tilt, active neighbour points, note)."""
    if not points:
        return 0.0, [], NOTE_NO_POINTS
    if x < points[0][0]:
        return 0.0, [], NOTE_BELOW_FIRST
    last_t, last_v = points[-1]
    if x >= last_t:
        return float(last_v), [[float(last_t), float(last_v)]], NOTE_BEYOND_LAST
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t0 <= x < t1:
            tilt = v0 + (v1 - v0) * (x - t0) / (t1 - t0)
            return (float(tilt),
                    [[float(t0), float(v0)], [float(t1), float(v1)]],
                    NOTE_INTERPOLATED)
    return float(last_v), [[float(last_t), float(last_v)]], NOTE_BEYOND_LAST


def _contract_view(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Eight-field view (sealed 455 rule): the 'unified' block is the truth
    when present, otherwise the flat payload. Returns (view, is_unified)."""
    unified = payload.get("unified")
    if isinstance(unified, dict):
        return unified, True
    return payload, False


def _declared_unknown(view: dict[str, Any], name: str) -> bool:
    for key in UNKNOWN_LISTS:
        unknown = view.get(key)
        if isinstance(unknown, (list, tuple)) and name in unknown:
            return True
    return False


def _read_number(view: dict[str, Any], unified: bool,
                 field: str) -> float | None:
    """A real measured number or None -- never a fabricated fallback (the
    455 pattern). Flat payloads are read ONLY by their wire key (so the
    direction word or 466's 0/1 confidence flag is never misread as a
    contract number); unified blocks by the contract name."""
    source = field if unified else FIELD_SOURCES[field]
    if _declared_unknown(view, source) or (
            source != field and _declared_unknown(view, field)):
        return None
    raw = view.get(source)
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def _read_state(view: dict[str, Any], unified: bool) -> str | None:
    names = (FIELD_STATE,) if unified else (STATE_SOURCE, FIELD_STATE)
    for name in names:
        if _declared_unknown(view, name):
            return None
        raw = view.get(name)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().upper()
    return None


def _side_applies(side: str, value: float) -> bool:
    if side == SIDE_ABS:
        return True
    if side == SIDE_UP:
        return value > 0.0
    return value < 0.0
