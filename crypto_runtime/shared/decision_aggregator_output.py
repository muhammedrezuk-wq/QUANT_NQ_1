from __future__ import annotations

import time
from typing import Any

EVENT_OUT = "decision.aggregated.state"
DECISION_ID_PREFIX = "dec:"
STATUS_OK = "ok"
STATUS_INCOMPLETE = "insufficient_data"
QUALITY_GOOD = "good"
QUALITY_LOW = "low"
KIND_DIRECTIONAL = "directional"
DIR_BUY = "buy"
DIR_SELL = "sell"
REASON_NO_CALIBRATION = "NO_CALIBRATION"
STATE_AGG_SECTIONS = ("150", "200", "250", "300", "350", "400")
STATE_STALE = "STALE"
STATE_ANALYZING = "ANALYZING"
STATE_NOT_READY = "NOT_READY"
STATE_READY = "READY"

# v3.0.0 (2026-08-25, owner vision "nobody waits for anybody"): the decision
# card is derived from the ROOM -- the freshest known value per source -- on
# every validated tick. The old batch model demanded every expected family
# inside ONE cycle window (~200ms between ticks) and closed the previous
# cycle as "superseded" before it could ever complete: measured 204,236
# cycles, complete=0, superseded=204,237. Presence is now freshness-based:
# a row is FRESH while its age is inside its family's declared horizon, and
# "complete" means every expected family has fresh evidence -- readiness
# stays a separate, gradual, weight-gated fact (active_weight / aggregate
# state), exactly as the eight-field contract intends.


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result == result else fallback


def _measured(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def family_of(source: str) -> str:
    return source.split(":", 1)[0].removesuffix("-live")


async def publish_live_decision(atom: Any, scope: tuple[str, str, str]) -> None:
    if atom._context is None:
        return
    identity = atom._identity.get(scope)
    if identity is None:
        return
    account, broker, symbol = scope
    now = time.monotonic()
    store = atom._evidence_store.get(scope, {})
    evidence: list[dict[str, Any]] = []
    families: set[str] = set()
    for source, stored in sorted(store.items()):
        row = dict(stored)
        age = now - float(row.pop("received_mono", now))
        horizon = atom._family_horizon(family_of(source))
        row["age_s"] = round(age, 3)
        row["fresh"] = age <= horizon
        evidence.append(row)
        if row["fresh"]:
            families.add(family_of(source))
    missing = sorted(set(atom._expected) - families)
    complete = not missing
    for family in missing:
        atom._missing_family_counts[family] = (
            atom._missing_family_counts.get(family, 0) + 1)
    if complete:
        atom._on_completion += 1
    fresh_rows = [row for row in evidence if row["fresh"]]
    fresh_directional = sum(
        row["kind"] == KIND_DIRECTIONAL and row["direction"] in (DIR_BUY, DIR_SELL)
        for row in fresh_rows
    )
    warnings = [] if fresh_directional else ["NO_FRESH_DIRECTIONAL_EVIDENCE"]
    if not complete:
        warnings.append("INCOMPLETE_DECISION_CYCLE")

    weighted_rows = [row for row in fresh_rows if row.get("weight_known")]
    available_weight = sum(_number(row.get("weight")) for row in weighted_rows)
    active_weight = sum(_number(row.get("weight_effect")) for row in weighted_rows)
    missing_weight = max(0.0, available_weight - active_weight)
    calibrated = available_weight > 0
    if calibrated and active_weight > 0:
        weighted_direction = sum(
            _number(row.get("direction_value")) * _number(row.get("weight_effect"))
            for row in weighted_rows
        ) / active_weight
        weighted_strength = sum(
            _number(row.get("strength")) * _number(row.get("weight_effect"))
            for row in weighted_rows
        ) / active_weight
        weighted_confidence = sum(
            _number(row.get("confidence")) * _number(row.get("weight_effect"))
            for row in weighted_rows
        ) / active_weight
    else:
        weighted_direction = weighted_strength = weighted_confidence = None
    weight_reason = "" if calibrated else REASON_NO_CALIBRATION
    if not calibrated:
        warnings.append(REASON_NO_CALIBRATION)
    elif missing_weight > 0:
        warnings.append("SECTION_WEIGHT_MISSING")
    if calibrated and active_weight <= 0:
        warnings.append("NO_READY_SECTION_WEIGHT")

    depth_unknown_fields: list[str] = []
    depth_aggregates: dict[str, float | None] = {}
    for depth_field in ("current_depth", "required_depth"):
        depth_total = depth_weight = 0.0
        for row in weighted_rows:
            depth_value = _measured(row.get(depth_field))
            if depth_value is None:
                continue
            effect = _number(row.get("weight_effect"))
            depth_total += depth_value * effect
            depth_weight += effect
        if depth_weight > 0:
            depth_aggregates[depth_field] = round(depth_total / depth_weight, 4)
        else:
            depth_aggregates[depth_field] = None
            depth_unknown_fields.append(depth_field)

    # The six-section aggregate state comes from the room -- the freshest
    # card per section, whatever its state (a STALE family must surface as
    # STALE, never as silently absent).
    section_states: dict[str, str] = {}
    for section_id, row in atom._room.get(scope, {}).items():
        state = str(row.get("state") or "").strip().upper()
        if state and state != "UNKNOWN":
            section_states[section_id] = state
    agg_states = [section_states[sid] for sid in STATE_AGG_SECTIONS if sid in section_states]
    state_missing_sections = [sid for sid in STATE_AGG_SECTIONS if sid not in section_states]
    if not agg_states:
        aggregate_state = STATE_NOT_READY
    elif any(state == STATE_STALE for state in agg_states):
        aggregate_state = STATE_STALE
    elif len(agg_states) < len(STATE_AGG_SECTIONS) or any(
        state == STATE_ANALYZING for state in agg_states
    ):
        aggregate_state = STATE_ANALYZING
    elif all(state == STATE_READY for state in agg_states):
        aggregate_state = STATE_READY
    else:
        aggregate_state = STATE_NOT_READY

    cycle_id = str(identity.get("cycle_id") or "")
    await atom._context.publish(EVENT_OUT, {
        "account_id": account or None,
        "broker": broker or None,
        "symbol": symbol,
        "id": "decision_aggregator",
        "cycle_id": cycle_id,
        "decision_id": DECISION_ID_PREFIX + cycle_id,
        "timeframe": identity.get("timeframe"),
        "period_start": identity.get("period_start"),
        "status": STATUS_OK if complete else STATUS_INCOMPLETE,
        "cycle_status": "complete" if complete else "incomplete",
        "complete": complete,
        "signal": (
            "up" if weighted_direction is not None and weighted_direction > 0
            else "down" if weighted_direction is not None and weighted_direction < 0
            else "sideways"
        ),
        "score": round(weighted_direction, 4) if weighted_direction is not None else None,
        "confidence": round(weighted_confidence, 4) if weighted_confidence is not None else None,
        "strength": round(weighted_strength, 4) if weighted_strength is not None else None,
        "quality": QUALITY_GOOD if complete and fresh_directional else QUALITY_LOW,
        "warnings": warnings,
        "evidence": evidence,
        "evidence_count": len(evidence),
        "fresh_evidence_count": len(fresh_rows),
        "fresh_directional": fresh_directional,
        "weighted_direction": round(weighted_direction, 4) if weighted_direction is not None else None,
        "weighted_strength": round(weighted_strength, 4) if weighted_strength is not None else None,
        "current_depth": depth_aggregates["current_depth"],
        "required_depth": depth_aggregates["required_depth"],
        "depth_unknown_fields": depth_unknown_fields,
        "aggregate_state": aggregate_state,
        "state_missing_sections": state_missing_sections,
        "weight_reason": weight_reason,
        "calibrated": calibrated,
        "available_weight": round(available_weight, 4),
        "active_weight": round(active_weight, 4),
        "missing_weight": round(missing_weight, 4),
        "weighted_sections": sorted(row["source"] for row in weighted_rows),
        "metadata": {
            "timeframe": identity.get("timeframe"),
            "expected_families": list(atom._expected),
            "families_present": sorted(families),
            "missing_families": missing,
            "close_reason": "live",
            "cycle_policy": "room_freshness",
        },
    })
    atom._emitted += 1
