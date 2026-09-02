from __future__ import annotations

import asyncio
import time
from typing import Any

import clock
import rules_store
from ruling_math import *
from ruling_math import _valid_points, _finite, interpolate, _contract_view, _declared_unknown, _read_number, _read_state, _side_applies, _DP, _BUSY_TIMEOUT_MS, _DB_TIMEOUT_S, _SCHEMA

# Campaign 450-901 batch B: the gate-passed handler (the actual tilt engine)
# extracted verbatim.

async def _on_gate_passed(atom, payload: dict[str, Any]) -> None:
    if not atom._running or atom._context is None or not isinstance(payload, dict):
        return
    gate_state = str(payload.get("gate_state") or GATE_STATE_PASSED).upper()
    if gate_state != GATE_STATE_PASSED:
        # Defensive: only the PASSED gate feeds the engine. A blocked or
        # recorded decision generates nothing (S29; acceptance test 8).
        atom._ignored_not_passed += 1
        return
    symbol = str(payload.get("symbol") or "")
    if not symbol:
        return
    atom._gate_seen += 1
    view, unified = _contract_view(payload)
    contributions: dict[str, dict[str, Any]] = {}
    total_raw = 0.0
    for field in CURVE_FIELDS:
        value = _read_number(view, unified, field)
        entry = atom._contribution(field, value)
        contributions[field] = entry
        total_raw += float(entry["tilt"])
    # S13: weight is a contribution factor, never a ladder -- displayed,
    # zero tilt (no approved aggregation formula uses it yet).
    contributions[FIELD_WEIGHT] = {
        "value": _read_number(view, unified, FIELD_WEIGHT), "tilt": 0.0,
        "curve_active": [], "note": NOTE_NOT_CURVABLE, "curves": {}}
    # S15/S41: state is a barrier, not a number.
    state_value = _read_state(view, unified)
    contributions[FIELD_STATE] = {
        "value": state_value, "tilt": 0.0, "curve_active": [],
        "note": NOTE_BARRIER, "curves": {}}
    if state_value == STATE_READY:
        ruling = RULING_ALLOW
    elif state_value in BLOCK_ALL_STATES:
        ruling = RULING_BLOCK_ALL
    else:
        # NOT_READY, ANALYZING, unknown, anything else: not READY, so it
        # cannot allow an increase (S15: the engine never converts a
        # non-ready state into ready). Prevention only -- no numeric
        # mitigation is invented (S41).
        ruling = RULING_BLOCK_INCREASE
    cap = atom._tilt_max_total
    total_capped = min(max(total_raw, -cap), cap)
    if ruling == RULING_BLOCK_ALL:
        total_capped = 0.0
    elif ruling == RULING_BLOCK_INCREASE:
        total_capped = min(total_capped, 0.0)
    out: dict[str, Any] = {"id": ID_ENGINE}
    for name in IDENTITY_FIELDS:
        out[name] = payload.get(name)
    out.update({
        "gate_request_id": payload.get("gate_request_id"),
        "decision_side": payload.get("decision_side"),
        "contributions": contributions,
        "total_raw": round(total_raw, _DP),
        "total_capped": round(total_capped, _DP),
        "tilt_max_total": cap,
        "state_barrier": {"state": state_value, "ruling": ruling},
        "source_timestamp": payload.get("gated_at"),
        "restored": False})
    atom._last_tilt[symbol] = out
    atom._tilt_published += 1
    await atom._emit(EVENT_TILT_STATE, out)
    stamp = _finite(payload.get("gated_at"))
    # Blocking sqlite write -- fires on every approved decision, so it runs
    # off the event loop thread (found during the 304-atom audit: it used to
    # block the loop for up to busy_timeout=10s under store contention).
    await asyncio.to_thread(
        atom._journal, symbol, str(payload.get("decision_id") or ""),
        contributions, round(total_capped, _DP),
        stamp if stamp is not None else time.time())
