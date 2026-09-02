"""Wait-state fixer and explainer inside the single decision path.

NQ seal item 22, batch B (B2 per paper Q8, dials B3 per verdict Q6):
457 receives the SAME decision cycle as the side checkers -- the output of
453 (decision.scored.state) -- and answers one question only: is the system
waiting, and why? It never converts a wait into a side, never overrides the
conflict resolution (458 owns arbitration) and never creates an order.

Published status (task contract):
* "inactive"  -- some side qualifies under the Q6 thresholds; the wait
  interpreter steps aside (reason names which side; when BOTH qualify the
  arbitration belongs to 458 -- 457 does not resolve it).
* "eligible"  -- the wait IS the active state: no side qualifies. The reason
  carries the first known blocker (blocking_field / blocking_value /
  blocking_threshold when they are known), e.g. confidence below the
  required floor, insufficient depth, state not READY...

An unknown field blocks side eligibility with a declared
"FIELD_UNKNOWN:<name>" -- never read as zero, never a false measurement.
Ratio carries NO invented meaning or threshold (Q6 verdict text).

This atom owns the shared Q6 floors (DECISION_MIN_STRENGTH,
DECISION_ELIGIBILITY_MIN_CONFIDENCE, DECISION_MIN_CURRENT_DEPTH) -- it is
the checker that consumes them for BOTH sides to explain waiting -- and
re-reads the two direction dials (owned by 455/456) each cycle through
effective_value().
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

ATOM_VERSION = "1.3.0"
ATOM_ID = "457"

EVENT_IN = "decision.scored.state"
EVENT_OUT = "decision.wait.state"

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

ID_WAIT = "wait_state"

STATUS_OK = "ok"
STATUS_ELIGIBLE = "eligible"        # the wait is active
STATUS_INACTIVE = "inactive"        # a side qualifies -- no wait

SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDE_BOTH = "both"

WARN_IDENTITY_INCOMPLETE = "identity_incomplete"

REASON_FIELD_UNKNOWN = "FIELD_UNKNOWN:%s"
REASON_BUY_ELIGIBLE = "BUY_SIDE_ELIGIBLE"
REASON_SELL_ELIGIBLE = "SELL_SIDE_ELIGIBLE"
REASON_BOTH_ELIGIBLE = "BOTH_SIDES_ELIGIBLE"
REASON_DIRECTION = "DIRECTION_INSUFFICIENT"
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
#: The three shared floors are owned here; the direction dials are owned
#: by 455/456 and re-read each cycle.
DIALS_READ = {
    "DECISION_BUY_MIN_DIRECTION": ("_buy_min_direction", "buy_min_direction"),
    "DECISION_SELL_MIN_DIRECTION": ("_sell_min_direction", "sell_min_direction"),
    "DECISION_MIN_STRENGTH": ("_min_strength", "min_strength"),
    "DECISION_ELIGIBILITY_MIN_CONFIDENCE": ("_min_confidence", "min_confidence"),
    "DECISION_MIN_CURRENT_DEPTH": ("_min_current_depth", "min_current_depth"),
}
DIALS_OWNED = {
    "DECISION_MIN_STRENGTH": "_min_strength",
    "DECISION_ELIGIBILITY_MIN_CONFIDENCE": "_min_confidence",
    "DECISION_MIN_CURRENT_DEPTH": "_min_current_depth",
}


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
        self._buy_min_direction = 50.0
        self._sell_min_direction = 50.0
        self._min_strength = 45.0
        self._min_confidence = 63.0
        self._min_current_depth = 45.0
        self._seen = 0
        self._emitted = 0
        self._waits = 0
        self._inactive = 0
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
            "id": "decision_dials_457", "atom_id": ATOM_ID, "status": STATUS_OK,
            "dials": {"DECISION_MIN_STRENGTH": self._min_strength,
                      "DECISION_ELIGIBILITY_MIN_CONFIDENCE": self._min_confidence,
                      "DECISION_MIN_CURRENT_DEPTH": self._min_current_depth,
                      "DECISION_BUY_MIN_DIRECTION": self._buy_min_direction,
                      "DECISION_SELL_MIN_DIRECTION": self._sell_min_direction}})

    async def start(self) -> None:
        self._running = True
        await self._publish_dials_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_scored(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        self._seen += 1
        self._refresh_dials()
        view = _contract_view(payload)

        direction = _known_number(view, "direction", FIELD_SOURCES["direction"])
        strength = _known_number(view, "strength", FIELD_SOURCES["strength"])
        confidence = _known_number(view, "confidence", FIELD_SOURCES["confidence"])
        depth = _known_number(view, "current_depth", FIELD_SOURCES["current_depth"])
        state = _known_state(view)

        sell_threshold = -self._sell_min_direction
        direction_buy_ok = direction is not None and direction >= self._buy_min_direction
        direction_sell_ok = direction is not None and direction <= sell_threshold
        strength_ok = strength is not None and strength >= self._min_strength
        confidence_ok = confidence is not None and confidence >= self._min_confidence
        depth_ok = depth is not None and depth >= self._min_current_depth
        state_ok = state == STATE_READY

        checks = [
            _check("direction_buy", direction, self._buy_min_direction, direction_buy_ok),
            _check("direction_sell", direction, sell_threshold, direction_sell_ok),
            _check("strength", strength, self._min_strength, strength_ok),
            _check("confidence", confidence, self._min_confidence, confidence_ok),
            _check("current_depth", depth, self._min_current_depth, depth_ok),
            _check("state", state, STATE_READY, state_ok),
        ]
        shared_ok = strength_ok and confidence_ok and depth_ok and state_ok
        buy_eligible = direction_buy_ok and shared_ok
        sell_eligible = direction_sell_ok and shared_ok

        blocking_field = None
        blocking_value: Any = None
        blocking_threshold: Any = None
        reason: str | None = None
        if buy_eligible and sell_eligible:
            # Both sides qualify: 457 does NOT arbitrate -- 458 owns that.
            status, reason, eligible_side = STATUS_INACTIVE, REASON_BOTH_ELIGIBLE, SIDE_BOTH
        elif buy_eligible:
            status, reason, eligible_side = STATUS_INACTIVE, REASON_BUY_ELIGIBLE, SIDE_BUY
        elif sell_eligible:
            status, reason, eligible_side = STATUS_INACTIVE, REASON_SELL_ELIGIBLE, SIDE_SELL
        else:
            status, eligible_side = STATUS_ELIGIBLE, None
            if direction is None:
                reason = REASON_FIELD_UNKNOWN % "direction"
                blocking_field = "direction"
            elif not direction_buy_ok and not direction_sell_ok:
                reason = REASON_DIRECTION
                blocking_field = "direction"
                blocking_value = direction
                blocking_threshold = (self._buy_min_direction if direction >= 0
                                      else sell_threshold)
            else:
                # Direction qualifies one side; name the first shared blocker.
                for name, value, threshold, ok, known_reason in (
                        ("strength", strength, self._min_strength,
                         strength_ok, REASON_STRENGTH),
                        ("confidence", confidence, self._min_confidence,
                         confidence_ok, REASON_CONFIDENCE),
                        ("current_depth", depth, self._min_current_depth,
                         depth_ok, REASON_DEPTH),
                        ("state", state, STATE_READY, state_ok, REASON_STATE)):
                    if ok:
                        continue
                    blocking_field = name
                    blocking_value = value
                    blocking_threshold = threshold
                    reason = (REASON_FIELD_UNKNOWN % name if value is None
                              else known_reason)
                    break

        identity, missing = _identity_of(payload)
        warnings = [WARN_IDENTITY_INCOMPLETE] if missing else []
        if missing:
            self._identity_warnings += 1
        if status == STATUS_ELIGIBLE:
            self._waits += 1
        else:
            self._inactive += 1
        self._last = "%s reason=%s" % (status, reason)
        await self._context.publish(EVENT_OUT, {
            "id": ID_WAIT,
            "account_id": identity["account_id"], "broker": identity["broker"],
            "symbol": identity["symbol"] or symbol,
            "timeframe": identity["timeframe"],
            "period_start": identity["period_start"],
            "decision_id": identity["decision_id"],
            "cycle_id": str(payload.get("cycle_id") or ""),
            "status": status, "reason": reason, "checks": checks,
            "eligible_side": eligible_side,
            "blocking_field": blocking_field,
            "blocking_value": blocking_value,
            "blocking_threshold": blocking_threshold,
            "warnings": warnings, "missing_identity": missing,
            "source_timestamp": payload.get("source_timestamp",
                                            payload.get("period_start")),
            "timestamp": clock.now(),
            "metadata": {"buy_min_direction": self._buy_min_direction,
                         "sell_min_direction": self._sell_min_direction,
                         "min_strength": self._min_strength,
                         "min_confidence": self._min_confidence,
                         "min_current_depth": self._min_current_depth,
                         "required_state": STATE_READY}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "emitted": self._emitted,
                   "waits": self._waits, "inactive": self._inactive,
                   "identity_warnings": self._identity_warnings,
                   "last": self._last,
                   "dials": {"buy_min_direction": self._buy_min_direction,
                             "sell_min_direction": self._sell_min_direction,
                             "min_strength": self._min_strength,
                             "min_confidence": self._min_confidence,
                             "min_current_depth": self._min_current_depth,
                             "applied": self._dials_applied}}
        if not self._seen:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d waits=%d inactive=%d last=%s" % (
                self._seen, self._waits, self._inactive, self._last),
            details=details)
