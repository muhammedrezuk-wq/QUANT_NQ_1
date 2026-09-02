from __future__ import annotations

import time
from collections import deque
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import stale_after_s
from shared.tick_contract import VALIDATED_TICK_EVENT, as_validated_tick
from shared.decision_aggregator_output import publish_live_decision
from shared.decision_aggregator_health import health as decision_health
from shared.decision_room import emit_room

ATOM_VERSION = "3.0.1"
# v3.0.0 (2026-08-25, owner vision + nq seal): the BATCH is gone. The old
# model opened a cycle per validated tick, demanded every expected family
# inside that ~200ms window (issubset gate), and closed the previous cycle
# "superseded" before it could complete -- measured: 204,236 cycles,
# complete=0, superseded=204,237, top_missing=400 in 100%. Nobody waits for
# anybody now: every source's LATEST delivery is held with its age, a row is
# fresh while inside its family's declared horizon, and the decision card is
# derived from that room on every tick. Readiness stays gradual and
# weight-gated -- presence and readiness are different facts.
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_STRATEGIES = "strategy.cycle.collected"
EVENT_PROBABILITIES = "probability.cycle.collected"
EVENT_CONFIDENCE = "probability.confidence.state"
EVENT_STRUCTURE = "market.structure.updated"
EVENT_LIQUIDITY = "market.liquidity.updated"
EVENT_STATS = "stats.cycle.collected"
EVENT_OUT = "decision.aggregated.state"
EVENT_ROOM = "decision.room.state"
EVENT_SECTION_LIVE: dict[str, str] = {
    "structure.section.live": "200", "liquidity.section.live": "250",
    "stats.section.live": "300", "probability.section.live": "350",
    "strategy.section.live": "400",
    # X.md Build 4 connection 3 (owner seal 2026-08-23): analysis enters as a
    # SECTION with ONE card, family "150" -- the old analysis.raw.completed
    # path stays CLOSED (duplicate evidence would double the section weight).
    "analysis.section.live": "150"}
REASON_IDENTITY = "IDENTITY_INCOMPLETE"
_DIAGNOSTIC_CAP = 128
ID_AGG = "decision_aggregator"
# NQ seal item 22 batch B (B1): the decision_id is BORN here,
# deterministically, from the current cycle identity -- a replay of the same
# cycle re-derives the same id, and every hop downstream carries it as-is.
DECISION_ID_PREFIX = "dec:"
STATUS_OK = "ok"
QUALITY_GOOD = "good"
QUALITY_LOW = "low"
DIR_BUY = "buy"; DIR_SELL = "sell"
DIR_NEUTRAL = "neutral"; DIR_UNKNOWN = "unknown"
KIND_DIRECTIONAL = "directional"; KIND_CONTEXT = "context"
REASON_NOT_STARTED = "NOT_STARTED"; REASON_NO_CYCLES = "NO_CYCLES_YET"
STATE_READY_FLAT = "READY"
_FAMILY_BLOCKING_STATES = frozenset({"ERROR", "INVALID", "STALE"})
_DIRECTION_WORDS = {"buy": DIR_BUY, "long": DIR_BUY, "up": DIR_BUY, "bullish": DIR_BUY,
    "sell": DIR_SELL, "short": DIR_SELL, "down": DIR_SELL, "bearish": DIR_SELL,
    "sideways": DIR_NEUTRAL, "range": DIR_NEUTRAL, "ranging": DIR_NEUTRAL,
    "neutral": DIR_NEUTRAL, "flat": DIR_NEUTRAL}

def _number(value: Any, fallback: float = 0.0) -> float:
    try: result = float(value)
    except (TypeError, ValueError): return fallback
    return result if result == result else fallback

def _measured(value: Any) -> float | None:
    # NQ seal item 22 (A8): a real measurement or None -- never a 0 fallback.
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if result == result else None

def _direction(value: Any) -> str:
    return _DIRECTION_WORDS.get(str(value or "").strip().lower(), DIR_UNKNOWN)

def _quality(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in (QUALITY_GOOD, QUALITY_LOW) else QUALITY_GOOD

def _contract_field(contract: dict[str, Any], payload: dict[str, Any],
                    field: str, fallback_field: str | None = None) -> float | None:
    # A8 + room truth: a field the contract DECLARED unknown must enter the
    # room as None -- a zeroed compatibility value is not a measurement.
    if field in contract:
        if field in set(contract.get("unknown_fields") or []):
            return None
        return _measured(contract.get(field))
    return _measured(payload.get(field, payload.get(fallback_field)
                                 if fallback_field else None))

class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None; self._running = False
        self._expected: tuple[str, ...] = ()
        #: per-family freshness horizon overrides (seconds) from config;
        #: the default horizon is the owner's approved STALE_AFTER_S dial.
        self._family_freshness: dict[str, float] = {}
        #: latest tick identity per scope -- the decision identity source.
        self._identity: dict[tuple[str, str, str], dict[str, Any]] = {}
        #: the evidence room: latest row per source per scope, with its age.
        self._evidence_store: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
        #: the section room: latest card per section per scope (any state).
        self._room: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
        self._ticks_seen = self._emitted = self._on_completion = 0
        self._updates = self._invalid = 0
        self._missing_family_counts: dict[str, int] = {}
        self._health_seen = {"invalid": 0}
        self._section_live_received = 0; self._section_live_admitted = 0
        self._section_live_seen: dict[tuple[str, str, str, str], int] = {}
        self._section_live_rejected: dict[str, int] = {}
        self._section_live_diagnostics: deque[dict[str, Any]] = deque(maxlen=_DIAGNOSTIC_CAP)
        self._room_updates = 0
        self._room_emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._expected = tuple(str(value) for value in context.config["expected_families"])
        overrides = context.config.get("family_freshness_s") or {}
        if isinstance(overrides, dict):
            self._family_freshness = {
                str(family): float(seconds)
                for family, seconds in overrides.items()
                if _measured(seconds) is not None and float(seconds) > 0}
        context.subscribe(EVENT_TICK, self._on_tick)
        # v3.0.0: the strategies' collected cycle is subscribed again -- it is
        # the ONLY directional evidence source (452 admits kind=directional
        # only), and it had been disconnected: the handler existed with no
        # subscription, so zero directional evidence could ever reach the
        # gate. Measured root of "neutral 16,360/16,360".
        context.subscribe(EVENT_STRATEGIES, self._on_strategies)
        context.subscribe(EVENT_PROBABILITIES, self._on_probabilities)
        context.subscribe(EVENT_CONFIDENCE, self._on_confidence)
        context.subscribe(EVENT_STRUCTURE, self._on_structure)
        context.subscribe(EVENT_LIQUIDITY, self._on_liquidity)
        context.subscribe(EVENT_STATS, self._on_stats)
        for event in EVENT_SECTION_LIVE:
            context.subscribe(event, self._on_section_live)

    async def start(self) -> None: self._running = True
    async def stop(self) -> None: self._running = False
    async def shutdown(self) -> None: await self.stop()

    def _family_horizon(self, family: str) -> float:
        override = self._family_freshness.get(family)
        return override if override is not None else stale_after_s()

    def _scope_of(self, payload: dict[str, Any]) -> tuple[str, str, str] | None:
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not account or not broker or not symbol:
            self._invalid += 1
            return None
        return (account, broker, symbol)

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        tick = as_validated_tick(payload)
        symbol = str(tick.get("symbol") or ""); account = str(tick.get("account_id") or "")
        broker = str(tick.get("broker") or ""); cycle_id = str(tick.get("cycle_id") or "")
        if not symbol or not account or not broker or not cycle_id:
            self._invalid += 1; return
        scope = (account, broker, symbol.upper())
        self._identity[scope] = {
            "cycle_id": cycle_id,
            "timeframe": str(tick.get("timeframe") or "tick"),
            "period_start": tick.get("period_start")}
        self._ticks_seen += 1
        await publish_live_decision(self, scope)

    def _store(self, scope: tuple[str, str, str], source: str,
               row: dict[str, Any]) -> None:
        row["received_mono"] = time.monotonic()
        self._evidence_store.setdefault(scope, {})[source] = row
        self._updates += 1

    def _evidence(self, scope: tuple[str, str, str], cycle_id: str, source: str,
                  label: str, kind: str, direction: str, score: float | None,
                  confidence: float, quality: str, status: str,
                  **extra: Any) -> None:
        row = {"source": source, "label": label, "kind": kind,
               "direction": direction, "score": score, "confidence": confidence,
               "quality": quality, "cycle_id": cycle_id, "status": status}
        if extra: row.update(extra)
        self._store(scope, source, row)

    def _absorb_units(self, payload: dict[str, Any], family: str,
                      context_only: bool) -> None:
        scope = self._scope_of(payload)
        results = payload.get("results")
        if scope is None or not isinstance(results, dict): return
        cycle_id = str(payload.get("cycle_id") or "")
        for unit_id, unit in results.items():
            if not isinstance(unit, dict): self._invalid += 1; continue
            metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
            # v3.0.1: the tick-native strategy units SPEAK NUMBERS (direction
            # on the +-100 contract scale), not buy/sell words -- the word
            # table alone read every unit as UNKNOWN, so ALL evidence landed
            # kind=context and 452 had zero eligible rows forever (measured
            # live on the firehose: 31/31 CONTEXT_ONLY). A measured numeric
            # direction is the signal: >0 buy, <0 sell, 0 neutral.
            numeric_direction = _measured(unit.get("direction"))
            if numeric_direction is not None:
                direction = (DIR_BUY if numeric_direction > 0
                             else DIR_SELL if numeric_direction < 0
                             else DIR_NEUTRAL)
            else:
                direction = _direction(unit.get("direction") or metadata.get("direction"))
            if direction == DIR_UNKNOWN and not context_only:
                direction = _direction(unit.get("signal"))
            probability = _number(metadata.get("probability"), -1.0)
            score = _number(unit.get("score"))
            kind = KIND_CONTEXT if context_only or direction == DIR_UNKNOWN else KIND_DIRECTIONAL
            source = "%s:%s" % (family, unit_id)
            unified = unit.get("unified") if isinstance(unit.get("unified"), dict) else {}
            unknown_fields = set(unified.get("unknown_fields") or [])
            direction_value = _measured(unified.get("direction"))
            strength_value = _measured(unified.get("strength"))
            weight_value = _measured(unified.get("weight"))
            weight_known = (not {"direction", "strength", "weight"} & unknown_fields
                            and direction_value is not None and strength_value is not None
                            and weight_value is not None)
            self._evidence(scope, str(unit.get("cycle_id") or cycle_id), source,
                str(unit_id), kind, direction, score,
                _number(unit.get("confidence")), _quality(unit.get("quality")),
                str(unit.get("status") or STATUS_OK),
                raw_signal=str(unit.get("signal") or ""),
                probability=probability if probability >= 0 else None,
                unified_state=str(unified.get("state") or ""),
                current_depth=_measured(unified.get("current_depth")),
                direction_value=direction_value, strength=strength_value,
                weight=weight_value,
                weight_effect=_measured(unified.get("weight_effect")) or 0.0,
                weight_known=weight_known)

    def _family_blocked(self, payload: dict[str, Any]) -> bool:
        # Block only explicitly terminal states -- the family's own card on
        # the section channel surfaces STALE/ERROR/INVALID in the aggregate.
        unified = payload.get("unified")
        state = str((unified.get("state") if isinstance(unified, dict) else None)
                    or payload.get("state") or "").strip().upper()
        return state in _FAMILY_BLOCKING_STATES

    async def _on_strategies(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        if self._family_blocked(payload): return
        self._absorb_units(payload, "400", False)

    async def _on_probabilities(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        if self._family_blocked(payload): return
        self._absorb_units(payload, "350", True)

    async def _on_confidence(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        scope = self._scope_of(payload)
        if scope is None: return
        self._evidence(scope, str(payload.get("cycle_id") or ""), "359",
            "probability_confidence", KIND_CONTEXT, DIR_UNKNOWN,
            _number(payload.get("score")), _number(payload.get("confidence")),
            _quality(payload.get("quality")),
            str(payload.get("status") or STATUS_OK),
            raw_signal=str(payload.get("signal") or ""))

    async def _absorb_section(self, payload: dict[str, Any], source: str,
                              label: str) -> None:
        if not self._running or not isinstance(payload, dict): return
        scope = self._scope_of(payload)
        if scope is None: return
        unified = payload.get("unified") if isinstance(payload.get("unified"), dict) else {}
        unknown_fields = set(unified.get("unknown_fields") or [])
        direction_value = _measured(unified.get("direction"))
        strength_value = _measured(unified.get("strength"))
        weight_value = _measured(unified.get("weight"))
        weight_known = (not {"direction", "strength", "weight"} & unknown_fields
                        and direction_value is not None and strength_value is not None
                        and weight_value is not None)
        self._evidence(scope, str(payload.get("cycle_id") or ""), source, label,
            KIND_CONTEXT, _direction(payload.get("signal")),
            _number(payload.get("score")), _number(payload.get("confidence")),
            _quality(payload.get("quality")),
            str(payload.get("status") or STATUS_OK),
            unified_state=str(unified.get("state") or ""),
            direction_value=direction_value, strength=strength_value,
            weight=weight_value,
            weight_effect=_measured(unified.get("weight_effect")) or 0.0,
            weight_known=weight_known)

    def _reject_section_live(self, reason: str, scope: tuple[str, ...],
                             section_id: str, state: str) -> None:
        self._section_live_rejected[reason] = self._section_live_rejected.get(reason, 0) + 1
        self._section_live_diagnostics.append({"section_id": section_id,
            "scope": list(scope), "unified_state": state, "reason": reason,
            "admitted": False})

    async def _on_section_live(self, payload: dict[str, Any]) -> None:
        # v3.0.0: EVERY fresh section card enters the room with its declared
        # state -- presence and readiness are different facts. Only READY
        # cards contribute weight (weight_effect) to the decision numbers;
        # a NOT_READY section is present-but-not-contributing, exactly the
        # owner's "its weight just isn't there". Identity stays mandatory.
        if not self._running or not isinstance(payload, dict): return
        self._section_live_received += 1
        unified = payload.get("unified") if isinstance(payload.get("unified"), dict) else None
        section_id = str(payload.get("section_id") or "")
        state = str((unified.get("state") if unified is not None else None)
                    or payload.get("state") or "")
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        symbol = str(payload.get("symbol") or "").strip().upper()
        scope_key = (account, broker, symbol, section_id)
        self._section_live_seen[scope_key] = self._section_live_seen.get(scope_key, 0) + 1
        if not account or not broker or not symbol or not section_id:
            self._reject_section_live(REASON_IDENTITY, scope_key, section_id, state)
            return
        scope = (account, broker, symbol)
        contract = unified or {}
        state_name = state.strip().upper() or "UNKNOWN"
        is_section_ready = state_name == STATE_READY_FLAT
        direction_value = _contract_field(contract, payload, "direction", "score")
        strength_value = _contract_field(contract, payload, "strength")
        weight_value = _contract_field(contract, payload, "weight")
        measured_row = (direction_value is not None and strength_value is not None
                        and weight_value is not None)
        self._room.setdefault(scope, {})[section_id] = {
            "section_id": section_id, "state": state_name,
            "direction": direction_value,
            "direction_sign": _measured(contract.get("direction_sign")),
            "strength": strength_value,
            "confidence": _contract_field(contract, payload, "confidence"),
            "current_depth": _contract_field(contract, payload, "current_depth"),
            "required_depth": _contract_field(contract, payload, "required_depth"),
            "weight": weight_value,
            "ratio": _contract_field(contract, payload, "ratio"),
            "unknown_fields": list(contract.get("unknown_fields") or []),
            "readiness_pct": _measured(payload.get("readiness_pct")),
            "timeframe": str(payload.get("timeframe") or ""),
            "period_start": payload.get("period_start"),
            "received_mono": time.monotonic()}
        self._room_updates += 1
        await emit_room(self, account, broker, symbol)
        if is_section_ready:
            self._section_live_admitted += 1
            self._section_live_diagnostics.append({"section_id": section_id,
                "scope": list(scope_key), "unified_state": state, "reason": "",
                "admitted": True})
        else:
            self._reject_section_live(state_name or "NOT_READY", scope_key,
                                      section_id, state)
        self._evidence(scope, str(payload.get("cycle_id") or ""),
            "%s-live" % section_id, "section_live_%s" % section_id,
            KIND_CONTEXT, _direction(payload.get("signal")),
            _measured(payload.get("score")), _number(payload.get("confidence")),
            _quality(payload.get("quality")),
            str(payload.get("status") or STATUS_OK), unified_state=state_name,
            direction_value=direction_value, strength=strength_value,
            weight=weight_value,
            ratio=_contract_field(contract, payload, "ratio"),
            weight_effect=(weight_value if (is_section_ready and measured_row)
                           else 0.0),
            weight_known=measured_row,
            current_depth=_contract_field(contract, payload, "current_depth"),
            required_depth=_contract_field(contract, payload, "required_depth"),
            live=True)

    async def _on_structure(self, payload: dict[str, Any]) -> None:
        await self._absorb_section(payload, "210", "market_structure")

    async def _on_liquidity(self, payload: dict[str, Any]) -> None:
        await self._absorb_section(payload, "260", "market_liquidity")

    async def _on_stats(self, payload: dict[str, Any]) -> None:
        await self._absorb_section(payload, "300", "stats_cycle")

    async def health_check(self) -> HealthStatus:
        return decision_health(self)
