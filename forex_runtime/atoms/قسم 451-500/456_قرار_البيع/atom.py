"""Sell-side eligibility checker inside the single decision path.

NQ seal item 22, batch B (B2 per paper Q8, dials B3 per verdict Q6):
456 receives the SAME decision cycle as everyone else -- the output of 453
(decision.scored.state) -- applies the owner's Q6 acceptance thresholds to
the eight-field contract values it carries, and publishes an eligibility
STATE. It is not a decision, not an order, and never an arbitration:

* it re-analyzes nothing (no ticks, no candles);
* it never changes direction/strength/confidence/depth/ratio;
* it bypasses no filter and never resolves a conflict (458 owns that);
* an unknown field blocks eligibility with a declared reason
  ("FIELD_UNKNOWN:<name>") -- it is never read as a fabricated zero and
  never counted as a false measured failure (Q8 rule).

Sell direction rule (Q6, literal): directional value <= -50.0000. The dial
DECISION_SELL_MIN_DIRECTION stores the positive magnitude (bounds 0..100)
and is applied to the negative side: direction <= -dial.

Ratio carries NO invented meaning or threshold (Q6 verdict text), and the
required state (READY) is a structural condition, not a numeric dial.
This atom owns DECISION_SELL_MIN_DIRECTION and re-reads the shared floors
(owned by 457) through effective_value() at every cycle.
"""
from __future__ import annotations

from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.decision_dials import (EVENT_COMMAND as EVENT_DIALS_COMMAND,
                                   EVENT_STATE as EVENT_DIALS_STATE,
                                   apply_command, effective_value)
from shared.parameter_registry import ParameterRegistry
from shared.unified_contract import STATE_READY

ATOM_VERSION = "1.4.0"
ATOM_ID = "456"

EVENT_IN = "decision.scored.state"
EVENT_OUT = "decision.eligibility.sell.state"

# NQ seal item 22 batch B (wiring the eight): contract field -> the key 453
# v3.8.0 actually publishes it under. The +-100 signed value is
# direction_value (453 keeps the word in "direction" -- measured live before
# this wiring: FIELD_UNKNOWN:direction on every cycle, because the word was
# read as a number); strength_value/confidence_value are the same measured
# ratios rescaled x100 to the 0-100 scale of the Q6 thresholds;
# current_depth passes from 451's weighted aggregate. Check rows and
# FIELD_UNKNOWN reasons keep the contract names.
FIELD_SOURCES = {"direction": "direction_value",
                 "strength": "strength_value",
                 "confidence": "confidence_value",
                 "current_depth": "current_depth"}
# Owner ruling 2026-08-20 (B NQ): the READY check reads aggregate_state --
# 451's path-merge state rule applied across the six sections -- passed
# through 452/453. FIELD_UNKNOWN:state only when the payload truly lacks it.
STATE_SOURCE = "aggregate_state"

ID_ELIGIBILITY = "sell_eligibility"
SIDE = "sell"

STATUS_OK = "ok"
STATUS_ELIGIBLE = "eligible"
STATUS_NOT_ELIGIBLE = "not_eligible"

WARN_IDENTITY_INCOMPLETE = "identity_incomplete"

REASON_FIELD_UNKNOWN = "FIELD_UNKNOWN:%s"
REASON_DIRECTION = "DIRECTION_ABOVE_THRESHOLD"
REASON_STRENGTH = "STRENGTH_BELOW_THRESHOLD"
REASON_CONFIDENCE = "CONFIDENCE_BELOW_THRESHOLD"
REASON_DEPTH = "DEPTH_BELOW_THRESHOLD"
REASON_STATE = "STATE_NOT_READY"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"

#: The six-part parent decision identity (Q8: it must pass without loss).
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "timeframe",
                   "period_start", "decision_id")

#: Dials this checker reads: registry name -> (attribute, config key).
#: DECISION_SELL_MIN_DIRECTION is owned here (commands applied by 456);
#: the shared floors are owned by 457 and re-read each cycle.
DIALS_READ = {
    "DECISION_SELL_MIN_DIRECTION": ("_sell_min_direction", "sell_min_direction"),
    "DECISION_MIN_STRENGTH": ("_min_strength", "min_strength"),
    "DECISION_ELIGIBILITY_MIN_CONFIDENCE": ("_min_confidence", "min_confidence"),
    "DECISION_MIN_CURRENT_DEPTH": ("_min_current_depth", "min_current_depth"),
}
DIALS_OWNED = {"DECISION_SELL_MIN_DIRECTION": "_sell_min_direction"}


def _contract_view(payload: dict[str, Any]) -> dict[str, Any]:
    """Eight-field view: the sealed 'unified' block is the truth when present
    (same admission rule 451 applies), otherwise the flat payload."""
    unified = payload.get("unified")
    return unified if isinstance(unified, dict) else payload


def _declared_unknown(view: dict[str, Any], name: str) -> bool:
    unknown = view.get("unknown_fields")
    return isinstance(unknown, (list, tuple)) and name in unknown


def _known_number(view: dict[str, Any], name: str,
                  source: str | None = None) -> float | None:
    """A real measured number or None -- never a fabricated fallback.
    The value is read from `source` (the key 453 publishes it under); an
    unknown declared under either the source or the contract name blocks."""
    source = source or name
    if _declared_unknown(view, source) or (
            source != name and _declared_unknown(view, name)):
        return None
    raw = view.get(source)
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def _known_state(view: dict[str, Any]) -> str | None:
    if _declared_unknown(view, STATE_SOURCE) or _declared_unknown(view, "state"):
        return None
    raw = view.get(STATE_SOURCE)
    if not isinstance(raw, str):
        return None
    text = raw.strip().upper()
    return text or None


def _check(name: str, value: Any, threshold: Any, passed: bool) -> dict[str, Any]:
    return {"name": name, "value": value, "threshold": threshold,
            "passed": bool(passed)}


def _identity_of(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The six identity fields as published upstream -- absent stays None
    (declared via missing list), never invented here (Q8 staged rollout)."""
    identity: dict[str, Any] = {}
    missing: list[str] = []
    for key in IDENTITY_FIELDS:
        raw = payload.get(key)
        if key == "period_start":
            value = raw if raw not in (None, "") else None
        else:
            value = str(raw) if raw not in (None, "") else None
        identity[key] = value
        if value is None:
            missing.append(key)
    return identity, missing


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._registry: ParameterRegistry | None = None
        self._cfg: dict[str, float] = {}
        self._sell_min_direction = 50.0
        self._min_strength = 45.0
        self._min_confidence = 63.0
        self._min_current_depth = 45.0
        self._seen = 0
        self._emitted = 0
        self._eligible = 0
        self._not_eligible = 0
        self._identity_warnings = 0
        self._last = "-"
        self._dials_applied = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._cfg = {key: float(context.config[key])
                     for _, key in DIALS_READ.values()}
        try:
            self._registry = ParameterRegistry()
        except Exception:  # noqa: BLE001 -- registry briefly unavailable
            self._registry = None
        self._refresh_dials()
        context.subscribe(EVENT_IN, self._on_scored)
        context.subscribe(EVENT_DIALS_COMMAND, self._on_dial_command)

    def _refresh_dials(self) -> None:
        for name, (attr, key) in DIALS_READ.items():
            setattr(self, attr,
                    effective_value(name, self._cfg[key], self._registry))

    async def _on_dial_command(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        applied = apply_command(payload, atom_id=ATOM_ID)
        if applied is None:
            return
        setattr(self, DIALS_OWNED[applied["name"]], float(applied["value"]))
        self._dials_applied += 1
        await self._publish_dials_state()

    async def _publish_dials_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_DIALS_STATE, {
            "id": "decision_dials_456", "atom_id": ATOM_ID, "status": STATUS_OK,
            "dials": {"DECISION_SELL_MIN_DIRECTION": self._sell_min_direction,
                      "DECISION_MIN_STRENGTH": self._min_strength,
                      "DECISION_ELIGIBILITY_MIN_CONFIDENCE": self._min_confidence,
                      "DECISION_MIN_CURRENT_DEPTH": self._min_current_depth}})

    async def start(self) -> None:
        self._running = True
        await self._publish_dials_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _evaluate(self, view: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        """Q6 sell-side conditions over the eight arriving fields. Every
        condition is a checks row with its value, threshold and verdict.
        Unknown -> passed=False with FIELD_UNKNOWN -- not a false measurement.
        """
        checks: list[dict[str, Any]] = []
        failures: list[str] = []

        direction = _known_number(view, "direction", FIELD_SOURCES["direction"])
        applied_threshold = -self._sell_min_direction
        passed = direction is not None and direction <= applied_threshold
        checks.append(_check("direction", direction, applied_threshold, passed))
        if not passed:
            failures.append(REASON_FIELD_UNKNOWN % "direction"
                            if direction is None else REASON_DIRECTION)

        strength = _known_number(view, "strength", FIELD_SOURCES["strength"])
        passed = strength is not None and strength >= self._min_strength
        checks.append(_check("strength", strength, self._min_strength, passed))
        if not passed:
            failures.append(REASON_FIELD_UNKNOWN % "strength"
                            if strength is None else REASON_STRENGTH)

        confidence = _known_number(view, "confidence", FIELD_SOURCES["confidence"])
        passed = confidence is not None and confidence >= self._min_confidence
        checks.append(_check("confidence", confidence, self._min_confidence, passed))
        if not passed:
            failures.append(REASON_FIELD_UNKNOWN % "confidence"
                            if confidence is None else REASON_CONFIDENCE)

        depth = _known_number(view, "current_depth", FIELD_SOURCES["current_depth"])
        passed = depth is not None and depth >= self._min_current_depth
        checks.append(_check("current_depth", depth, self._min_current_depth, passed))
        if not passed:
            failures.append(REASON_FIELD_UNKNOWN % "current_depth"
                            if depth is None else REASON_DEPTH)

        state = _known_state(view)
        passed = state == STATE_READY
        checks.append(_check("state", state, STATE_READY, passed))
        if not passed:
            failures.append(REASON_FIELD_UNKNOWN % "state"
                            if state is None else REASON_STATE)
        return checks, failures

    async def _on_scored(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        self._seen += 1
        self._refresh_dials()
        view = _contract_view(payload)
        checks, failures = self._evaluate(view)
        eligible = not failures
        status = STATUS_ELIGIBLE if eligible else STATUS_NOT_ELIGIBLE
        reason = None if eligible else failures[0]
        identity, missing = _identity_of(payload)
        warnings = [WARN_IDENTITY_INCOMPLETE] if missing else []
        if missing:
            self._identity_warnings += 1
        if eligible:
            self._eligible += 1
        else:
            self._not_eligible += 1
        self._last = "%s reason=%s" % (status, reason)
        await self._context.publish(EVENT_OUT, {
            "id": ID_ELIGIBILITY, "side": SIDE,
            "account_id": identity["account_id"], "broker": identity["broker"],
            "symbol": identity["symbol"] or symbol,
            "timeframe": identity["timeframe"],
            "period_start": identity["period_start"],
            "decision_id": identity["decision_id"],
            "cycle_id": str(payload.get("cycle_id") or ""),
            "status": status, "reason": reason, "checks": checks,
            "warnings": warnings, "missing_identity": missing,
            "source_timestamp": payload.get("source_timestamp",
                                            payload.get("period_start")),
            "timestamp": clock.now(),
            "metadata": {"sell_min_direction": self._sell_min_direction,
                         "min_strength": self._min_strength,
                         "min_confidence": self._min_confidence,
                         "min_current_depth": self._min_current_depth,
                         "required_state": STATE_READY}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "emitted": self._emitted,
                   "eligible": self._eligible, "not_eligible": self._not_eligible,
                   "identity_warnings": self._identity_warnings,
                   "last": self._last,
                   "dials": {"sell_min_direction": self._sell_min_direction,
                             "min_strength": self._min_strength,
                             "min_confidence": self._min_confidence,
                             "min_current_depth": self._min_current_depth,
                             "applied": self._dials_applied}}
        if not self._seen:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d eligible=%d not_eligible=%d last=%s" % (
                self._seen, self._eligible, self._not_eligible, self._last),
            details=details)
