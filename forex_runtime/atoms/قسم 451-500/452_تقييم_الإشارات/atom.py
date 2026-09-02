from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.decision_dials import (EVENT_COMMAND as EVENT_DIALS_COMMAND,
                                   EVENT_STATE as EVENT_DIALS_STATE,
                                   apply_command, effective_value)

ATOM_VERSION = "2.4.1"

EVENT_IN = "decision.aggregated.state"
EVENT_MODEL = "learning.model.evidence"
EVENT_OUT = "decision.evaluated.state"

# NQ seal item 22 batch B (wiring the eight) + owner ruling 2026-08-20 (B NQ,
# path-merge state rule applied across sections): the aggregated decision
# depth AND the aggregated cross-section state are measured at 451 and cross
# this hop AS-IS, exactly like the six identity fields -- present keys are
# republished unchanged (None stays None, unknowns declared upstream via
# depth_unknown_fields/state_missing_sections) and an absent key is never
# invented here.
AGGREGATE_PASSTHROUGH_FIELDS = ("current_depth", "required_depth",
                                "depth_unknown_fields", "aggregate_state",
                                "state_missing_sections")

# NQ seal item 22 batch B (B1, ruling Q9 s17): the six-field decision identity
# must cross every hop unharmed. 452 used to drop the broker here (measured in
# the scan) -- now the whole identity is read from the input and republished.
# A missing field is republished as None (never invented) and declared in the
# "identity_incomplete" warning together with the missing field names.
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "timeframe",
                   "period_start", "decision_id")
WARN_IDENTITY_INCOMPLETE = "identity_incomplete"


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

ID_EVAL = "signal_evaluator"
STATUS_OK = "ok"
QUALITY_GOOD = "good"
QUALITY_LOW = "low"

DIR_BUY = "buy"
DIR_SELL = "sell"
KIND_DIRECTIONAL = "directional"

REASON_ELIGIBLE = "ELIGIBLE"
REASON_STALE = "STALE_CYCLE"
REASON_NO_DIRECTION = "NO_DIRECTION"
REASON_NOT_DIRECTIONAL = "CONTEXT_ONLY"
REASON_BAD_STATUS = "SOURCE_NOT_OK"
REASON_NO_CONFIDENCE = "NO_CONFIDENCE"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"

_BAD_STATUS = ("insufficient_data", "unavailable", "error", "failed", "degraded")


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result == result else fallback


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._low_quality_factor = 0.5
        self._min_confidence = 0.0
        self._model: dict[str, dict[str, Any]] = {}
        self._seen = 0
        # X.md Build 3 (2026-08-23): a dropped input must be COUNTED with its
        # reason code -- never silently discarded (an atom that rejects 20k
        # inputs must say so, not just look "hungry").
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}
        self._emitted = 0
        self._eligible_total = 0
        self._dials_applied = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._low_quality_factor = effective_value(
            "DECISION_LOW_QUALITY_FACTOR", float(context.config["low_quality_factor"]))
        self._min_confidence = effective_value(
            "DECISION_MIN_CONFIDENCE", float(context.config["min_confidence"]))
        self._dials_applied = 0
        context.subscribe(EVENT_IN, self._on_aggregated)
        context.subscribe(EVENT_MODEL, self._on_model)
        context.subscribe(EVENT_DIALS_COMMAND, self._on_dial_command)

    _DIAL_ATTRS = {"DECISION_LOW_QUALITY_FACTOR": "_low_quality_factor",
                   "DECISION_MIN_CONFIDENCE": "_min_confidence"}

    async def _on_dial_command(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        applied = apply_command(payload, atom_id="452")
        if applied is None:
            return
        setattr(self, self._DIAL_ATTRS[applied["name"]], float(applied["value"]))
        self._dials_applied += 1
        await self._publish_dials_state()

    async def _publish_dials_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_DIALS_STATE, {
            "id": "decision_dials_452", "atom_id": "452", "status": STATUS_OK,
            "dials": {"DECISION_LOW_QUALITY_FACTOR": self._low_quality_factor,
                      "DECISION_MIN_CONFIDENCE": self._min_confidence}})

    async def start(self) -> None:
        self._running = True
        await self._publish_dials_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_model(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict) and payload.get("symbol"):
            self._model[str(payload["symbol"])] = dict(payload)

    def _judge(self, item: dict[str, Any]) -> dict[str, Any]:
        record = dict(item)
        direction = str(record.get("direction") or "")
        status = str(record.get("status") or STATUS_OK).lower()
        confidence = _number(record.get("confidence"))
        quality = str(record.get("quality") or QUALITY_GOOD).lower()
        factor = self._low_quality_factor if quality == QUALITY_LOW else 1.0
        if not record.get("fresh"):
            eligible, reason, factor = False, REASON_STALE, 0.0
        elif record.get("kind") != KIND_DIRECTIONAL:
            eligible, reason = False, REASON_NOT_DIRECTIONAL
        elif direction not in (DIR_BUY, DIR_SELL):
            eligible, reason = False, REASON_NO_DIRECTION
        elif status in _BAD_STATUS:
            eligible, reason, factor = False, REASON_BAD_STATUS, 0.0
        elif confidence <= self._min_confidence:
            eligible, reason = False, REASON_NO_CONFIDENCE
        else:
            eligible, reason = True, REASON_ELIGIBLE
        record["eligible"] = eligible
        record["quality_factor"] = round(factor, 6)
        record["eligibility_reason"] = reason
        return record

    async def _on_aggregated(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            self._dropped += 1
            self._drop_reasons["IDENTITY_MISSING"] = self._drop_reasons.get("IDENTITY_MISSING", 0) + 1
            return
        self._seen += 1
        rows = payload.get("evidence")
        rows = rows if isinstance(rows, list) else []
        cycle_complete = (payload.get("complete") is True
                          and str(payload.get("cycle_status") or "") == "complete"
                          and str(payload.get("status") or STATUS_OK) == STATUS_OK)
        judged = [self._judge(row) for row in rows if isinstance(row, dict)]
        if not cycle_complete:
            for row in judged:
                row["eligible"] = False
                row["quality_factor"] = 0.0
                row["eligibility_reason"] = REASON_BAD_STATUS
        eligible = [row for row in judged if row["eligible"]]
        self._eligible_total += len(eligible)
        model = self._model.get(symbol)
        identity, identity_missing = _identity_of(payload)
        warnings = ([] if eligible else ["NO_ELIGIBLE_EVIDENCE"]) \
            + ([] if cycle_complete else ["INCOMPLETE_DECISION_CYCLE"])
        if identity_missing:
            warnings.append(WARN_IDENTITY_INCOMPLETE)
        depth_fields = {key: payload[key] for key in AGGREGATE_PASSTHROUGH_FIELDS
                        if key in payload}
        await self._context.publish(EVENT_OUT, {
            **identity, "symbol": symbol, **depth_fields,
            "id": ID_EVAL, "cycle_id": str(payload.get("cycle_id") or ""),
            "status": STATUS_OK if cycle_complete else "insufficient_data",
            "cycle_status": "complete" if cycle_complete else "incomplete",
            "complete": cycle_complete,
            # NQ seal item 22 batch B (B6): 452 judges eligibility only -- it
            # computes no signal/score/confidence of its own. None declares that
            # honestly (same pattern as 451 v2.6.7); the old ""/0/0.0 published
            # a fabricated measured-neutral that could cross thresholds.
            "signal": None, "score": None, "confidence": None,
            "quality": QUALITY_GOOD if eligible else QUALITY_LOW,
            "warnings": warnings, "identity_missing": identity_missing,
            "evidence": judged, "eligible_count": len(eligible),
            "evidence_count": len(judged), "model_evidence": model,
            "metadata": {"low_quality_factor": self._low_quality_factor,
                         "min_confidence": self._min_confidence}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "emitted": self._emitted,
                   "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons),
                   "eligible_total": self._eligible_total}
        if not self._seen:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="evaluated=%d eligible=%d" % (self._emitted, self._eligible_total),
                            details=details)
