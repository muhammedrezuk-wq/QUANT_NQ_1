from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.decision_dials import (EVENT_COMMAND as EVENT_DIALS_COMMAND,
                                   EVENT_STATE as EVENT_DIALS_STATE,
                                   apply_command, effective_value)
from shared.live_analysis import DEFAULT_WEIGHTS, MODE_LIVE, STATE_READY
from shared.section_contract import section_atom

ATOM_VERSION = "2.8.0"

EVENT_IN = "analysis.cycle.collected"
EVENT_OUT = "analysis.raw.completed"
# X.md Build 4 connection 2 (owner seal 2026-08-23): the analysis section card
# -- same event family as structure/liquidity/stats/probability/strategy.
EVENT_SECTION = "analysis.section.live"
SECTION_ID = "150"
EVENT_FAST = "analysis.fast.state"
EVENT_SLOW = "analysis.slow.state"

SIGNAL_UP = "up"
SIGNAL_DOWN = "down"
SIGNAL_SIDEWAYS = "sideways"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_NO_VALID = "no_valid_analysis"
LIVE_STALE_AFTER_S = 5.0
SIDEWAYS_SCORE = 5.0

STATE_CARD_READY = "READY"
STATE_CARD_ANALYZING = "ANALYZING"
STATE_CARD_NOT_READY = "NOT_READY"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CYCLES = "NO_CYCLES_YET"

_VOTE = {SIGNAL_UP: 1, SIGNAL_DOWN: -1, SIGNAL_SIDEWAYS: 0}
_DIRECTIONS = (SIGNAL_UP, SIGNAL_DOWN, SIGNAL_SIDEWAYS)

_DIAL_FAST_WEIGHT = "ANALYSIS_FAST_WEIGHT"
_DIAL_SLOW_WEIGHT = "ANALYSIS_SLOW_WEIGHT"
_DIAL_FAST_DEPTH = "ANALYSIS_FAST_REQUIRED_DEPTH"
_DIAL_SLOW_DEPTH = "ANALYSIS_SLOW_REQUIRED_DEPTH"
_DIAL_STALE = "DECISION_LIVE_STALE_AFTER_S"

_MERGE_FIELDS = ("direction", "strength", "confidence", "current_depth")


def _num(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


def _opt(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _pct(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100.0 if 0.0 <= value <= 1.0 else value


@section_atom("150", "166")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._agree_threshold = 0.2
        self._live_stale_after_s = LIVE_STALE_AFTER_S
        self._fast_weight = 55.0
        self._slow_weight = 45.0
        # Owner seal 2026-08-23: 100% split EQUALLY across all existing
        # sections -- the section card carries the SECTION weight; fast/slow
        # stay the internal split of this section's own weight.
        self._section_weight = 100.0 / 6.0
        self._fast_required_depth = 60.0
        self._slow_required_depth = 60.0
        self._dials_applied = 0
        self._fused = 0
        self._fast_published = 0
        self._slow_published = 0
        self._section_published = 0
        self._last_fast: dict[str, dict[str, Any]] = {}
        self._last_slow: dict[str, dict[str, Any]] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._agree_threshold = float(cfg["agree_threshold"])
        self._live_stale_after_s = effective_value(
            _DIAL_STALE, float(cfg.get("live_stale_after_s", LIVE_STALE_AFTER_S)))
        self._section_weight = max(0.0, float(cfg.get("section_weight", 100.0 / 6.0)))
        self._fast_weight = effective_value(
            _DIAL_FAST_WEIGHT, float(cfg.get("fast_weight", 55.0)))
        self._slow_weight = effective_value(
            _DIAL_SLOW_WEIGHT, float(cfg.get("slow_weight", 45.0)))
        self._fast_required_depth = effective_value(
            _DIAL_FAST_DEPTH, float(cfg.get("fast_required_depth", 60.0)))
        self._slow_required_depth = effective_value(
            _DIAL_SLOW_DEPTH, float(cfg.get("slow_required_depth", 60.0)))
        context.subscribe(EVENT_IN, self._on_collected)
        context.subscribe(EVENT_DIALS_COMMAND, self._on_dial_command)

    async def _on_dial_command(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        applied = apply_command(payload, atom_id="166")
        if applied is None:
            return
        name = applied["name"]
        value = float(applied["value"])
        attr = {_DIAL_STALE: "_live_stale_after_s",
                _DIAL_FAST_WEIGHT: "_fast_weight",
                _DIAL_SLOW_WEIGHT: "_slow_weight",
                _DIAL_FAST_DEPTH: "_fast_required_depth",
                _DIAL_SLOW_DEPTH: "_slow_required_depth"}[name]
        setattr(self, attr, value)
        self._dials_applied += 1
        if name in (_DIAL_FAST_WEIGHT, _DIAL_SLOW_WEIGHT) and isinstance(payload, dict):
            counterpart = _DIAL_SLOW_WEIGHT if name == _DIAL_FAST_WEIGHT else _DIAL_FAST_WEIGHT
            rebalanced = apply_command({
                "name": counterpart, "value": 100.0 - value,
                "command_id": str(payload.get("command_id") or "") + ":rebalance",
                "operator": payload.get("operator"),
                "approved_at": payload.get("approved_at",
                                           payload.get("command_requested_at")),
            }, atom_id="166")
            if rebalanced is not None:
                other_attr = "_slow_weight" if counterpart == _DIAL_SLOW_WEIGHT else "_fast_weight"
                setattr(self, other_attr, float(rebalanced["value"]))
        await self._publish_dials_state()

    async def _publish_dials_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_DIALS_STATE, {
            "id": "decision_dials_166", "atom_id": "166", "status": STATUS_OK,
            "dials": {_DIAL_STALE: self._live_stale_after_s,
                      _DIAL_FAST_WEIGHT: self._fast_weight,
                      _DIAL_SLOW_WEIGHT: self._slow_weight,
                      _DIAL_FAST_DEPTH: self._fast_required_depth,
                      _DIAL_SLOW_DEPTH: self._slow_required_depth}})

    async def start(self) -> None:
        self._running = True
        await self._publish_dials_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _eight(self, direction: float | None, strength: float | None,
               confidence: float | None, current_depth: float | None,
               required_depth: float, weight: float, state: str) -> dict[str, Any]:
        unknown = [name for name, value in (
            ("direction", direction), ("strength", strength),
            ("confidence", confidence), ("current_depth", current_depth)) if value is None]
        ratio = (min(100.0, current_depth / required_depth * 100.0)
                 if current_depth is not None and required_depth > 0 else None)
        if ratio is None:
            unknown.append("ratio")
        # Unit 150 closure, phase 1 (owner order 2026-08-23): readiness is a
        # GRADUAL percentage, never a binary flip -- the card keeps `state`
        # for compatibility, and readiness_pct says how close the section is:
        # 36 -> 36.3 -> 40 -> 50, no jumps.
        readiness = round(ratio, 1) if ratio is not None else None
        if readiness is None:
            unknown.append("readiness_pct")
        return {"direction": round(direction, 4) if direction is not None else 0.0,
                "strength": round(strength, 4) if strength is not None else 0.0,
                "confidence": round(confidence, 4) if confidence is not None else 0.0,
                "current_depth": round(current_depth, 4) if current_depth is not None else 0.0,
                "required_depth": round(required_depth, 4),
                "weight": round(weight, 4),
                "ratio": round(ratio, 4) if ratio is not None else 0.0,
                "readiness_pct": readiness,
                "state": state, "unknown_fields": unknown}

    async def _on_collected(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if payload.get("analysis_mode") == MODE_LIVE:
            await self._on_live_collected(payload)
            return
        await self._on_candle_collected(payload)

    async def _on_candle_collected(self, payload: dict[str, Any]) -> None:
        results = payload.get("results") or {}
        base = {"symbol": str(payload.get("symbol", "")),
                "cycle_id": str(payload.get("cycle_id", "")), "id": "fusion",
                "timeframe": str(payload.get("timeframe", ""))}
        meta = {"expected": payload.get("expected", 0),
                "present": payload.get("present", 0),
                "complete": payload.get("complete", False)}
        expected = max(1, int(_num(payload.get("expected")) or len(DEFAULT_WEIGHTS)))
        present = int(_num(payload.get("present")))
        completeness = min(100.0, present / expected * 100.0)
        complete = payload.get("complete") is True and str(payload.get("cycle_status") or "complete") == "complete"
        # v2.7.0 (2026-08-25): an incomplete candle batch is FUSED from the
        # analysts that DID deliver -- the absentees are named, completeness
        # says how much arrived, and the owner's slow-depth dial decides
        # whether that much is enough for READY. The old branch answered an
        # incomplete batch with fabricated zeros (score 0, confidence 0.0)
        # and parked the slow path in ANALYZING forever -- one silent analyst
        # starved the whole section, which is exactly the waiting the owner's
        # vision forbids.
        incomplete_missing = list(payload.get("missing") or [])
        meta["missing"] = incomplete_missing
        valid = [s for s in results.values()
                 if isinstance(s, dict) and s.get("status") == STATUS_OK
                 and str(s.get("cycle_id") or "") == base["cycle_id"]
                 and str(s.get("symbol") or "") == base["symbol"]
                 and str(s.get("timeframe") or "") == base["timeframe"]]
        if not valid:
            meta["valid"] = 0
            await self._context.publish(EVENT_OUT, {
                **base, "account_id": payload.get("account_id"),
                "period_start": payload.get("period_start"),
                "cycle_status": "complete" if complete else "incomplete",
                "status": STATUS_INSUFFICIENT, "signal": SIGNAL_SIDEWAYS,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_NO_VALID], "contributors": {}, "agreement": 0.0,
                "metadata": meta})
            self._fused += 1
            await self._store_slow(payload, base, direction=None, strength=None,
                                   confidence=None, completeness=completeness,
                                   state=STATE_CARD_NOT_READY, quality=QUALITY_LOW)
            return
        signal, agreement, confidence, score, warnings, voters = self._fuse(valid)
        if not complete:
            warnings = ["INCOMPLETE_ANALYSIS_CYCLE"] + warnings
        contributors = {str(s.get("id")): {
            "signal": s.get("signal"), "score": s.get("score"),
            "confidence": s.get("confidence")} for s in valid}
        meta["valid"] = len(valid)
        await self._context.publish(EVENT_OUT, {
            **base, "account_id": payload.get("account_id"),
            "period_start": payload.get("period_start"),
            "cycle_status": "complete" if complete else "incomplete",
            "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence,
            "quality": QUALITY_GOOD if complete else QUALITY_LOW,
            "warnings": warnings,
            "contributors": contributors, "agreement": agreement, "metadata": meta})
        self._fused += 1
        # Owner stamp 2026-08-21 -- "READY with zero" was the lie the owner kept
        # seeing. Measured on the live feed: a candle cycle can complete with
        # seven contributors and NOT ONE directional voter, because the
        # contextual analysers speak their own vocabulary (engulfing, london,
        # week_close, weak, normal) by contract. The old code fused that into
        # direction 0.0 + confidence 0.0 and still stamped the card READY -- a
        # measured zero where there was no measurement at all. No voter now
        # means direction and confidence are UNKNOWN (declared in
        # unknown_fields) and the card is NOT_READY, whatever the completeness.
        no_voice = voters == 0
        slow_state = (STATE_CARD_NOT_READY if no_voice
                      else STATE_CARD_READY if completeness >= self._slow_required_depth
                      else STATE_CARD_ANALYZING)
        # Owner-facing scale (measured 2026-08-21): the candle analysers publish
        # confidence on 0..1 while the live tick kernel publishes 0..100. Handing
        # both to the dashboard unchanged made a 0.40 read as "0.4%" instead of
        # 40% -- a hundredfold understatement of a real number, which reads as a
        # dead engine. The card's own confidence already passes through `_pct`;
        # the per-analyser breakdown now uses the very same rule, one scale out.
        slow_contributors = {
            name: {**row, "confidence": _pct(_opt(row.get("confidence")))}
            for name, row in contributors.items()}
        await self._store_slow(payload, base, direction=float(score),
                               strength=None, confidence=_pct(_opt(confidence)),
                               completeness=completeness, state=slow_state,
                               quality=QUALITY_GOOD, contributors=slow_contributors)

    async def _store_slow(self, payload: dict[str, Any], base: dict[str, Any],
                          direction: float | None, strength: float | None,
                          confidence: float | None, completeness: float,
                          state: str, quality: str,
                          contributors: dict[str, Any] | None = None) -> None:
        symbol = base["symbol"]
        if not symbol:
            return
        eight = self._eight(direction, strength, confidence, completeness,
                            self._slow_required_depth, self._slow_weight, state)
        # Owner stamp 2026-08-21: the slow card now carries its per-analyzer
        # breakdown exactly as the fast card does. The numbers were already
        # computed for `analysis.raw.completed` and simply never reached this
        # card, so the dashboard had a slow row it could never fill -- an
        # empty column that looked like a dead engine instead of a missing
        # field. Empty dict = the cycle produced no valid analyzer, declared.
        card = {"path": "slow", "source": "candles",
                "account_id": payload.get("account_id") or None,
                "broker": str(payload.get("broker") or "").strip() or None,
                "symbol": symbol, "timeframe": base["timeframe"],
                "cycle_id": base["cycle_id"],
                "period_start": payload.get("period_start"),
                "contributors": contributors or {},
                "quality": quality, **eight}
        self._last_slow[symbol] = card
        self._slow_published += 1
        await self._context.publish(EVENT_SLOW, card)
        await self._publish_section(symbol)

    async def _on_live_collected(self, payload: dict[str, Any]) -> None:
        results = payload.get("results")
        account = str(payload.get("account_id") or "").strip()
        symbol = str(payload.get("symbol") or payload.get("asset") or "").strip().upper()
        if not account or not symbol or not isinstance(results, dict):
            return
        trigger_ts = _num(payload.get("timestamp"))
        valid: list[dict[str, Any]] = []
        contributors: dict[str, dict[str, Any]] = {}
        available_weight = 0.0
        for analyzer_id in DEFAULT_WEIGHTS:
            item = results.get(analyzer_id)
            if not isinstance(item, dict):
                available_weight += DEFAULT_WEIGHTS[analyzer_id]
                continue
            weight = max(0.0, min(100.0, _num(item.get("weight"))))
            available_weight += weight
            identity_ok = (str(item.get("account_id") or "") == account
                           and str(item.get("symbol") or "").upper() == symbol
                           and str(item.get("analyzer_id") or item.get("id") or "") == analyzer_id)
            confidence = max(0.0, min(100.0, _num(item.get("confidence"))))
            current_depth = max(0.0, min(100.0, _num(item.get("current_depth"))))
            required_depth = max(0.0, min(100.0, _num(item.get("required_depth"))))
            threshold = max(0.0, min(100.0, _num(
                item.get("confidence_threshold", item.get("threshold")))))
            source_ts = _num(item.get("source_timestamp"))
            fresh = bool(source_ts > 0 and (trigger_ts <= 0 or trigger_ts - source_ts <= self._live_stale_after_s))
            ready = bool(identity_ok and fresh and item.get("ready") is True
                         and item.get("analysis_state") == STATE_READY
                         and current_depth >= required_depth and confidence >= threshold)
            row = {"analyzer_id": analyzer_id, "included": ready,
                   "identity_valid": identity_ok, "fresh": fresh,
                   "weight": weight, "weight_applied": weight if ready else 0.0,
                   "score": _num(item.get("score")),
                   "strength": _opt(item.get("strength")),
                   "confidence": confidence,
                   "current_depth": current_depth, "ready": bool(item.get("ready") is True),
                   "analysis_state": item.get("analysis_state")}
            contributors[analyzer_id] = row
            if ready:
                valid.append(row)
        active_weight = sum(_num(item.get("weight_applied")) for item in valid)
        missing_weight = max(0.0, available_weight - active_weight)
        fast_meta = {
            "account_id": account,
            "broker": str(payload.get("broker") or "").strip() or None,
            "symbol": symbol,
            "cycle_id": str(payload.get("cycle_id") or ""),
            "sequence": payload.get("sequence"),
            "source_timestamp": payload.get("source_timestamp"),
            "timestamp": payload.get("timestamp"),
            "active_weight": round(active_weight, 4),
            "available_weight": round(available_weight, 4),
            "missing_weight": round(missing_weight, 4),
            "contributors": contributors,
        }
        if not valid or active_weight <= 0:
            fast_card = {"path": "fast", "source": "ticks", **fast_meta,
                         "quality": QUALITY_LOW, "warnings": [WARN_NO_VALID],
                         **self._eight(None, None, None, None,
                                       self._fast_required_depth, self._fast_weight,
                                       STATE_CARD_NOT_READY)}
        else:
            weighted_score_sum = sum(_num(item.get("score")) * _num(item.get("weight_applied"))
                                     for item in valid)
            score = max(-100.0, min(100.0, weighted_score_sum / active_weight))
            confidence = sum(_num(item.get("confidence")) * _num(item.get("weight_applied"))
                             for item in valid) / active_weight
            current_depth = sum(_num(item.get("current_depth")) * _num(item.get("weight_applied"))
                                for item in valid) / active_weight
            strength_rows = [item for item in valid if item.get("strength") is not None]
            strength_mass = sum(_num(item.get("weight_applied")) for item in strength_rows)
            strength = (sum(_num(item.get("strength")) * _num(item.get("weight_applied"))
                            for item in strength_rows) / strength_mass
                        if strength_mass > 0 else None)
            fast_state = (STATE_CARD_READY
                          if current_depth >= self._fast_required_depth
                          else STATE_CARD_ANALYZING)
            fast_card = {"path": "fast", "source": "ticks", **fast_meta,
                         "quality": QUALITY_GOOD, "warnings": (
                             ["missing_weight"] if missing_weight > 0 else []),
                         **self._eight(score, strength, confidence, current_depth,
                                       self._fast_required_depth, self._fast_weight,
                                       fast_state)}
        self._last_fast[symbol] = fast_card
        self._fast_published += 1
        await self._context.publish(EVENT_FAST, dict(fast_card))
        await self._publish_section(symbol, live_trigger=payload)

    def _merge_paths(self, fast: dict[str, Any] | None,
                     slow: dict[str, Any] | None) -> dict[str, Any]:
        paths = []
        if fast is not None:
            paths.append((fast, max(0.0, self._fast_weight)))
        if slow is not None:
            paths.append((slow, max(0.0, self._slow_weight)))
        merged: dict[str, Any] = {}
        unknown: list[str] = []
        for field in _MERGE_FIELDS:
            mass = 0.0
            total = 0.0
            for card, weight in paths:
                if weight <= 0 or field in (card.get("unknown_fields") or []):
                    continue
                mass += weight
                total += _num(card.get(field)) * weight
            if mass > 0:
                merged[field] = total / mass
            else:
                merged[field] = None
                unknown.append(field)
        req_mass = sum(w for _, w in paths) or 1.0
        merged["required_depth"] = (sum(
            _num(card.get("required_depth")) * w for card, w in paths) / req_mass
            if paths else (self._fast_weight * self._fast_required_depth
                           + self._slow_weight * self._slow_required_depth) / 100.0)
        states = [str(card.get("state") or "") for card, w in paths if w > 0]
        # v2.7.0 (2026-08-25): one present path no longer parks the section in
        # ANALYZING -- nobody waits for anybody. A missing path is DECLARED
        # (`missing_path` warning + `path_missing_weight`) and the section's
        # state is the state of what actually delivered.
        if not states:
            state = STATE_CARD_NOT_READY
        elif any(s == "STALE" for s in states):
            state = "STALE"
        elif any(s == STATE_CARD_ANALYZING for s in states):
            state = STATE_CARD_ANALYZING
        elif all(s == STATE_CARD_READY for s in states):
            state = STATE_CARD_READY
        else:
            state = STATE_CARD_NOT_READY
        merged["state"] = state
        merged["unknown_fields"] = unknown
        return merged

    async def _publish_section(self, symbol: str,
                               live_trigger: dict[str, Any] | None = None) -> None:
        fast = self._last_fast.get(symbol)
        slow = self._last_slow.get(symbol)
        if fast is None and slow is None:
            return
        merged = self._merge_paths(fast, slow)
        direction = merged["direction"]
        strength = merged["strength"]
        confidence = merged["confidence"]
        current_depth = merged["current_depth"]
        required_depth = merged["required_depth"]
        eight = self._eight(direction, strength, confidence, current_depth,
                            required_depth, self._section_weight, merged["state"])
        present_paths = [name for name, card in (("fast", fast), ("slow", slow))
                         if card is not None]
        path_missing_weight = (0.0 if len(present_paths) == 2 else
                               (self._slow_weight if fast is not None else self._fast_weight))
        account = ((fast or {}).get("account_id")
                   or (slow or {}).get("account_id") or None)
        # 451 rejects live payloads without a broker (measured: every section
        # publish counted invalid at 451 until this field was carried through).
        broker = ((fast or {}).get("broker")
                  or (slow or {}).get("broker") or None)
        net = (_num(direction) / 100.0) if direction is not None else 0.0
        if direction is None:
            signal = None
        elif net > self._agree_threshold:
            signal = SIGNAL_UP
        elif net < -self._agree_threshold:
            signal = SIGNAL_DOWN
        else:
            signal = SIGNAL_SIDEWAYS
        ready = merged["state"] == STATE_CARD_READY
        trigger = live_trigger or {}
        body = {
            "account_id": account, "broker": broker,
            "symbol": symbol, "asset": symbol,
            "cycle_id": str(trigger.get("cycle_id")
                            or (fast or {}).get("cycle_id")
                            or (slow or {}).get("cycle_id") or ""),
            "id": "fusion", "timeframe": "section",
            "analysis_mode": MODE_LIVE, "live_contract_version": 2,
            "sequence": trigger.get("sequence"),
            "source_timestamp": trigger.get("source_timestamp"),
            "timestamp": trigger.get("timestamp"),
            "status": STATUS_OK if direction is not None else STATUS_INSUFFICIENT,
            "cycle_status": "live_latest",
            "analysis_state": STATE_READY if ready else "NOT_READY",
            "ready": ready,
            "signal": signal,
            "direction": eight["direction"] if direction is not None else None,
            "score": eight["direction"] if direction is not None else None,
            "strength": eight["strength"],
            "confidence": eight["confidence"],
            "current_depth": eight["current_depth"],
            "required_depth": eight["required_depth"],
            "weight": eight["weight"],
            "ratio": eight["ratio"],
            "quality": QUALITY_GOOD if direction is not None else QUALITY_LOW,
            "warnings": ([] if len(present_paths) == 2 else ["missing_path"]),
            "section_contract": eight,
            "paths": {"fast": fast, "slow": slow},
            "path_weights": {"fast": round(self._fast_weight, 4),
                             "slow": round(self._slow_weight, 4)},
            "path_missing_weight": round(path_missing_weight, 4),
            "contributors": (fast or {}).get("contributors") or {},
            "metadata": {"present_paths": present_paths,
                         "merge": "weighted_paths_v1"},
        }
        self._section_published += 1
        await self._context.publish(EVENT_OUT, body)
        # Build 4 connection 2: the same card on the section channel, shaped
        # exactly like the sibling sections (section_id admission at 451).
        # v2.7.0: the unified block carries the FULL eight-field contract with
        # honest Nones -- a zeroed compatibility value must never enter the
        # decision room as a measured 0 (A8).
        await self._context.publish(EVENT_SECTION, {
            **body, "section_id": SECTION_ID,
            "unified": {"state": merged["state"],
                        "direction": direction,
                        "strength": strength,
                        "confidence": confidence,
                        "current_depth": current_depth,
                        "required_depth": required_depth,
                        "weight": self._section_weight,
                        "weight_effect": self._section_weight if ready else 0.0,
                        "ratio": eight["ratio"] if current_depth is not None else None,
                        "readiness_pct": eight["readiness_pct"],
                        "unknown_fields": list(eight["unknown_fields"])}})

    def _fuse(self, valid: list[dict]) -> tuple:
        # Only a unit that speaks a direction votes (up/down/sideways). The
        # contextual candle analysers speak their own vocabulary -- engulfing,
        # london, week_close, weak, normal -- which the section contract
        # intends: directional votes, contextual does not. So a cycle can
        # complete with no directional voice at all, and that is UNKNOWN, not
        # zero. The count is returned so the caller can say so.
        directional = [s for s in valid if s.get("signal") in _DIRECTIONS]
        if not directional:
            return SIGNAL_SIDEWAYS, 0.0, 0.0, 0, [], 0
        conf_sum = sum(_num(s.get("confidence")) for s in directional)
        weighted = sum(_VOTE.get(s.get("signal"), 0) * _num(s.get("confidence"))
                       for s in directional)
        net = weighted / conf_sum if conf_sum > 0 else 0.0
        if net > self._agree_threshold:
            signal = SIGNAL_UP
        elif net < -self._agree_threshold:
            signal = SIGNAL_DOWN
        else:
            signal = SIGNAL_SIDEWAYS
        target = _VOTE[signal]
        agreeing = [s for s in directional if _VOTE.get(s.get("signal"), 0) == target]
        chosen = agreeing if agreeing else directional
        agreement = round(len(agreeing) / len(directional), 2)
        # Owner stamp 2026-08-21 -- the sideways verdict used to erase its own
        # evidence. A neutral analyser publishes score 0 by contract, so when
        # the panel fused to "sideways" the chosen set was exactly those zeros
        # and the card reported direction 0 AND confidence 0 forever. Measured:
        # the candle path published nothing but 0/0 for hours while five
        # analysers leaned up at 50-64 confidence. A lean that is too small to
        # be called a direction is still a measurement, not an absence.
        #   · direction  = the confidence-weighted net vote (-1..+1) on the
        #                  contract scale, i.e. the very number the neutral
        #                  band was compared against.
        #   · confidence = read across the whole directional panel, discounted
        #                  by how much of it agreed -- not across the zeros.
        if signal == SIGNAL_SIDEWAYS:
            score = int(round(net * 100.0))
            panel = directional
        else:
            score = int(round(sum(_num(s.get("score")) for s in chosen) / len(chosen)))
            panel = chosen
        confidence = round(
            agreement * (sum(_num(s.get("confidence")) for s in panel) / len(panel)), 2)
        warnings = ["conflict:" + str(s.get("id")) for s in directional
                    if signal != SIGNAL_SIDEWAYS and _VOTE.get(s.get("signal"), 0) == -target]
        return signal, agreement, confidence, score, warnings, len(directional)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"fused": self._fused, "fast_published": self._fast_published,
                   "slow_published": self._slow_published,
                   "section_published": self._section_published,
                   "fast_weight": self._fast_weight, "slow_weight": self._slow_weight,
                   "fast_required_depth": self._fast_required_depth,
                   "slow_required_depth": self._slow_required_depth,
                   "dials_applied": self._dials_applied}
        if self._fused == 0 and self._section_published == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CYCLES,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="fused=%d fast=%d slow=%d section=%d" % (
                self._fused, self._fast_published, self._slow_published,
                self._section_published),
            details=details)
