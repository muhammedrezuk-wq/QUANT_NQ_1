from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.decision_dials import (EVENT_COMMAND as EVENT_DIALS_COMMAND,
                                   EVENT_STATE as EVENT_DIALS_STATE,
                                   apply_command, effective_value)

ATOM_VERSION = "3.10.0"

EVENT_IN = "decision.evaluated.state"
EVENT_OUT = "decision.scored.state"

# NQ seal item 22 batch B (wiring the eight) + owner ruling 2026-08-20 (B NQ,
# path-merge state rule applied across sections): the aggregated decision
# depth AND the aggregated cross-section state are measured at 451 and cross
# this hop AS-IS -- present keys are republished unchanged (None stays None,
# unknowns declared upstream via depth_unknown_fields/state_missing_sections)
# and an absent key is never invented here.
AGGREGATE_PASSTHROUGH_FIELDS = ("current_depth", "required_depth",
                                "depth_unknown_fields", "aggregate_state",
                                "state_missing_sections", "confidence", "strength")

# NQ seal item 22 batch B (B1, ruling Q9 s17): the six-field decision identity
# crosses this hop complete -- read from the input, republished as-is. Missing
# fields are republished as None (never invented) and declared in the
# "identity_incomplete" warning with their names.
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

ID_SCORE = "score_calculator"
STATUS_OK = "ok"
QUALITY_GOOD = "good"
QUALITY_LOW = "low"

DIR_BUY = "buy"
DIR_SELL = "sell"
DIR_NEUTRAL = "neutral"

_ABSENT_REASONS = ("STALE_CYCLE", "SOURCE_NOT_OK")

WARN_LOW_PARTICIPATION = "LOW_PARTICIPATION"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"
_FULL_SCORE = 100.0


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result == result else fallback


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._directional: set[str] = set()
        self._directional_weight = 1.0
        self._context_weight = 0.0556
        self._min_participation = 0.20
        self._seen = 0
        # X.md Build 3 (2026-08-23): a dropped input must be COUNTED with its
        # reason code -- never silently discarded (an atom that rejects 20k
        # inputs must say so, not just look "hungry").
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}
        self._emitted = 0
        self._waits_low_participation = 0
        self._directional_verdicts = 0
        self._last_verdict = "-"
        self._dials_applied = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._directional_weight = effective_value(
            "DECISION_DIRECTIONAL_WEIGHT", float(context.config["directional_weight"]))
        self._context_weight = effective_value(
            "DECISION_CONTEXT_WEIGHT", float(context.config["context_weight"]))
        self._min_participation = effective_value(
            "DECISION_MIN_PARTICIPATION", float(context.config["min_participation"]))
        self._directional = {str(s) for s in context.config["directional_sources"]}
        self._dials_applied = 0
        context.subscribe(EVENT_IN, self._on_evaluated)
        context.subscribe(EVENT_DIALS_COMMAND, self._on_dial_command)

    _DIAL_ATTRS = {"DECISION_DIRECTIONAL_WEIGHT": "_directional_weight",
                   "DECISION_CONTEXT_WEIGHT": "_context_weight",
                   "DECISION_MIN_PARTICIPATION": "_min_participation"}

    async def _on_dial_command(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        applied = apply_command(payload, atom_id="453")
        if applied is None:
            return
        setattr(self, self._DIAL_ATTRS[applied["name"]], float(applied["value"]))
        self._dials_applied += 1
        await self._publish_dials_state()

    async def _publish_dials_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_DIALS_STATE, {
            "id": "decision_dials_453", "atom_id": "453", "status": STATUS_OK,
            "dials": {"DECISION_DIRECTIONAL_WEIGHT": self._directional_weight,
                      "DECISION_CONTEXT_WEIGHT": self._context_weight,
                      "DECISION_MIN_PARTICIPATION": self._min_participation}})

    async def start(self) -> None:
        self._running = True
        await self._publish_dials_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _weight_of(self, item: dict[str, Any]) -> float:
        source = str(item.get("source") or "")
        if source in self._directional:
            return self._directional_weight
        return self._context_weight

    async def _on_evaluated(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            self._dropped += 1
            self._drop_reasons["IDENTITY_MISSING"] = self._drop_reasons.get("IDENTITY_MISSING", 0) + 1
            return
        self._seen += 1
        cycle_complete = (payload.get("complete") is True
                          and str(payload.get("cycle_status") or "") == "complete"
                          and str(payload.get("status") or STATUS_OK) == STATUS_OK)
        rows = payload.get("evidence")
        rows = rows if isinstance(rows, list) else []
        contributions = []
        buy_total = 0.0
        sell_total = 0.0
        present_weight = 0.0
        spoken_weight = 0.0
        for item in rows:
            if not isinstance(item, dict):
                continue
            weight = self._weight_of(item)
            reason = str(item.get("eligibility_reason") or "")
            if item.get("eligible") or reason not in _ABSENT_REASONS:
                present_weight += weight
            if not cycle_complete or not item.get("eligible"):
                continue
            spoken_weight += weight
            share = _clamp(_number(item.get("score")) / _FULL_SCORE)
            confidence = _clamp(_number(item.get("confidence")))
            quality = _clamp(_number(item.get("quality_factor"), 1.0))
            value = weight * share * confidence * quality
            direction = str(item.get("direction") or "")
            if direction == DIR_BUY:
                buy_total += value
            elif direction == DIR_SELL:
                sell_total += value
            contributions.append({"source": item.get("source"), "label": item.get("label"),
                                  "direction": direction, "weight": round(weight, 6),
                                  "score": round(share * _FULL_SCORE, 4),
                                  "confidence": round(confidence, 6),
                                  "quality_factor": round(quality, 6),
                                  "contribution": round(value, 6)})
        spoken_mass = buy_total + sell_total
        net = buy_total - sell_total
        score = (abs(net) / spoken_mass * _FULL_SCORE) if spoken_mass > 0 else 0.0
        participation = (spoken_weight / present_weight) if present_weight > 0 else 0.0
        strength = (abs(net) / present_weight) if present_weight > 0 else 0.0
        direction = DIR_BUY if net > 0 else DIR_SELL if net < 0 else DIR_NEUTRAL
        identity, identity_missing = _identity_of(payload)
        warnings = [] if contributions else ["NO_ELIGIBLE_EVIDENCE"]
        if not cycle_complete: warnings.append("INCOMPLETE_DECISION_CYCLE")
        if identity_missing: warnings.append(WARN_IDENTITY_INCOMPLETE)
        if direction != DIR_NEUTRAL and participation < self._min_participation:
            direction = DIR_NEUTRAL
            warnings.append(WARN_LOW_PARTICIPATION)
            self._waits_low_participation += 1
        if direction != DIR_NEUTRAL:
            self._directional_verdicts += 1
        self._last_verdict = "%s s=%d p=%.2f/%.2f" % (
            direction, round(score), participation, self._min_participation)
        # NQ seal item 22 batch B (wiring the eight): direction_value re-encodes
        # the SAME published contract (the word is the signal, the score its
        # 0-100 magnitude) as one signed number: +score for buy, -score for
        # sell, 0.0 for neutral -- the FINAL word governs the sign, so a
        # low-participation demotion to neutral zeroes the signed value too.
        # strength_value/confidence_value are the same measured ratios
        # rescaled x100 to the 0-100 contract scale the Q6 thresholds live on
        # (a unit conversion, never a new measurement).
        published_score = round(score, 2)
        direction_value = (published_score if direction == DIR_BUY
                           else -published_score if direction == DIR_SELL else 0.0)
        depth_fields = {key: payload[key] for key in AGGREGATE_PASSTHROUGH_FIELDS
                        if key in payload}
        # Owner 2026-08-22: weighted confidence/strength come from 451's card (weighted
        # confidence/strength) via passthrough, not from participation. If present,
        # they are used for comparison in 455/456; otherwise the computed values (0-100) stay.
        weighted_conf = payload.get("confidence")
        weighted_str = payload.get("strength")
        out_conf = float(weighted_conf) if isinstance(weighted_conf, (int, float)) else None
        out_str = float(weighted_str) if isinstance(weighted_str, (int, float)) else None
        final_conf = round(out_conf, 4) if out_conf is not None else round(_clamp(participation) * 100.0, 4)
        final_str = round(out_str, 4) if out_str is not None else round(_clamp(strength) * 100.0, 4)
        await self._context.publish(EVENT_OUT, {
            **identity, "symbol": symbol,
            "id": ID_SCORE, "cycle_id": str(payload.get("cycle_id") or ""),
            "identity_missing": identity_missing,
            "status": STATUS_OK if cycle_complete else "insufficient_data",
            "cycle_status": "complete" if cycle_complete else "incomplete",
            "complete": cycle_complete,
            "signal": direction, "direction": direction,
            "direction_value": direction_value,
            "score": published_score, "confidence": round(_clamp(participation), 6),
            "participation": round(_clamp(participation), 6),
            "strength": round(_clamp(strength), 6),
            "strength_value": final_str,
            "confidence_value": final_conf,
            **depth_fields,
            "quality": QUALITY_GOOD if contributions else QUALITY_LOW,
            "warnings": warnings,
            "buy_total": round(buy_total, 6), "sell_total": round(sell_total, 6),
            "net": round(net, 6), "weight_present": round(present_weight, 6),
            "weight_available": round(present_weight, 6),
            "weight_spoken": round(spoken_weight, 6), "contributions": contributions,
            "evidence": rows,
            "metadata": {"directional_weight": self._directional_weight,
                         "context_weight": self._context_weight,
                         "directional_sources": sorted(self._directional),
                         "min_participation": self._min_participation}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "emitted": self._emitted,
                   "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons),
                   "directional_verdicts": self._directional_verdicts,
                   "waits_low_participation": self._waits_low_participation,
                   "last_verdict": self._last_verdict,
                   "dials": {"directional_weight": self._directional_weight,
                             "context_weight": self._context_weight,
                             "min_participation": self._min_participation,
                             "applied": self._dials_applied}}
        if not self._seen:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="scored=%d directional=%d lowpart_waits=%d last=%s" % (
                                self._emitted, self._directional_verdicts,
                                self._waits_low_participation, self._last_verdict),
                            details=details)
