from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.1.0"

EVENT_IN = "decision.filtered.state"
EVENT_OUT = "decision.approved.state"

METHOD = "final_gate"
ID_APPROVAL = "decision_approval"

# NQ seal item 22 batch B (B6): the decision side is its own vocabulary --
# decision_side in {"buy","sell","wait"} only. Legacy payloads without
# decision_side fall back to the word in "signal" ("neutral" -> wait);
# anything else is UNKNOWN and is rejected explicitly -- an unknown is not a
# known wait and never passes (or fails) a gate through a fake comparison.
SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDE_WAIT = "wait"
_LEGACY_NEUTRAL = "neutral"

STATUS_OK = "ok"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"

REASON_BLOCKED_UPSTREAM = "BLOCKED_UPSTREAM"
REASON_NO_ACTIONABLE_SIGNAL = "NO_ACTIONABLE_SIGNAL"
REASON_UPSTREAM_UNKNOWN = "UPSTREAM_RESULT_UNKNOWN"
REASON_SIDE_UNKNOWN = "DECISION_SIDE_UNKNOWN"

# Q9 s22: the rejected state is preserved with exactly these six fields.
STAGE_FILTER = "454"
STAGE_APPROVAL = "466"

# B1 (ruling Q9 s17): the six-field decision identity crosses this hop
# complete; a missing field is republished None (never invented) under the
# "identity_incomplete" warning with its name.
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "timeframe",
                   "period_start", "decision_id")
WARN_IDENTITY_INCOMPLETE = "identity_incomplete"


def _to_float(value: Any) -> float | None:
    # A8: a real measurement or None -- never a coerced 0.
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _identity_of(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    identity: dict[str, Any] = {}
    missing: list[str] = []
    for field in IDENTITY_FIELDS:
        value = payload.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            identity[field] = None
            missing.append(field)
        else:
            identity[field] = value
    return identity, missing


def _side_of(payload: dict[str, Any]) -> str | None:
    side = str(payload.get("decision_side") or "").strip().lower()
    if side in (SIDE_BUY, SIDE_SELL, SIDE_WAIT):
        return side
    legacy = str(payload.get("signal") or "").strip().lower()
    if legacy in (SIDE_BUY, SIDE_SELL, SIDE_WAIT):
        return legacy
    if legacy == _LEGACY_NEUTRAL:
        return SIDE_WAIT
    return None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._seen = 0
        self._approved = 0
        self._rejected = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_filtered)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_filtered(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        self._seen += 1
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        passed = meta.get("passed")
        side = _side_of(payload)
        # B7: the unified barrier list from 454 is passed through untouched.
        barriers = payload.get("barriers")
        barriers = barriers if isinstance(barriers, list) else None
        identity, identity_missing = _identity_of(payload)
        if passed is True and side in (SIDE_BUY, SIDE_SELL):
            approved, reason, stage = True, None, None
        elif passed is False:
            approved, reason, stage = False, REASON_BLOCKED_UPSTREAM, STAGE_FILTER
        elif passed is not True:
            # B6: 454's verdict never arrived -- unknown, not a known block.
            approved, reason, stage = False, REASON_UPSTREAM_UNKNOWN, STAGE_APPROVAL
        elif side is None:
            approved, reason, stage = False, REASON_SIDE_UNKNOWN, STAGE_APPROVAL
        else:
            approved, reason, stage = False, REASON_NO_ACTIONABLE_SIGNAL, STAGE_APPROVAL
        rejection = None
        if approved:
            self._approved += 1
        else:
            self._rejected += 1
            # Q9 s22: the rejected state preserves reason, stage, value,
            # threshold, time and the decision identity. Value/threshold come
            # from the first blocking barrier when 454 declared one (B7 quad);
            # otherwise they stay None (never invented).
            first = barriers[0] if barriers else None
            first = first if isinstance(first, dict) else None
            rejection = {
                "reason": (str(first.get("reason")) if first and first.get("reason")
                           else reason),
                "stage": stage,
                "value": (first.get("value") if first is not None
                          else (side if reason == REASON_NO_ACTIONABLE_SIGNAL else None)),
                "threshold": first.get("threshold") if first is not None else None,
                "time": time.time(),
                "decision_id": identity["decision_id"],
            }
        direction = str(payload.get("signal", "") or "")
        timeframe = str(payload.get("timeframe") or "")
        cycle_id = str(payload.get("cycle_id") or "")
        warnings = [] if approved else [reason]
        if identity_missing:
            warnings.append(WARN_IDENTITY_INCOMPLETE)
        await self._context.publish(EVENT_OUT, {
            **identity, "symbol": symbol, "id": ID_APPROVAL,
            "cycle_id": cycle_id, "status": STATUS_OK,
            "identity_missing": identity_missing,
            "approved": approved,
            "decision_side": side,
            "signal": direction if approved else "none",
            # v2.1.0 (nq seal 2026-08-25, dashboard-model finding): 454's
            # measured score crosses this hop AS-IS (value or None) -- the
            # old `if approved else 0` coerced a measured number into a fake
            # zero for every rejected decision, contradicting 454's own
            # honesty rule one hop earlier. Approval lives in `approved`.
            "score": _to_float(payload.get("score")),
            # 466 measures NO confidence and NO quality of its own -- the old
            # 1.0/0.0 and good/low were booleans dressed as measurements.
            # Declared unmeasured (None); consumers read `approved`.
            "confidence": None,
            "quality": None,
            "warnings": warnings,
            "barriers": barriers,
            "rejection": rejection,
            "metadata": {"method": METHOD, "timeframe": timeframe,
                         "direction": direction, "decision_side": side,
                         "approved": approved, "reason": reason,
                         "request_id": cycle_id}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d approved=%d rejected=%d emitted=%d" % (
                self._seen, self._approved, self._rejected, self._emitted),
            details={"seen": self._seen, "approved": self._approved,
                     "rejected": self._rejected, "emitted": self._emitted})
