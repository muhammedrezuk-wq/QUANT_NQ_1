from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.decision_dials import (EVENT_COMMAND as EVENT_DIALS_COMMAND,
                                   EVENT_STATE as EVENT_DIALS_STATE,
                                   apply_command, effective_value)

ATOM_VERSION = "3.1.0"

EVENT_IN = "decision.scored.state"
EVENT_OUT = "decision.resolved.state"
# NQ seal item 22 batch B (B1/B3, rulings Q8 s9-s10): 455/456/457 publish their
# eligibility verdicts under these agreed names (contract with the sibling
# batch); 458 stores the latest verdict per decision_id and settles the side.
EVENT_ELIGIBILITY_BUY = "decision.eligibility.buy.state"
EVENT_ELIGIBILITY_SELL = "decision.eligibility.sell.state"
EVENT_WAIT_STATE = "decision.wait.state"

ID_RESOLVE = "conflict_resolver"
STATUS_OK = "ok"
QUALITY_GOOD = "good"
QUALITY_LOW = "low"

DIR_BUY = "buy"
DIR_SELL = "sell"
DIR_NEUTRAL = "neutral"
DIR_WAIT = "wait"

# B6: the decision side is its own vocabulary -- exactly these three values,
# never mixed with the +-100 directional value.
SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDE_WAIT = "wait"
_RULE_TO_SIDE = {DIR_BUY: SIDE_BUY, DIR_SELL: SIDE_SELL,
                 DIR_NEUTRAL: SIDE_WAIT, DIR_WAIT: SIDE_WAIT}

STATUS_ELIGIBLE = "eligible"
# Origin of the settled side, recorded in the resolution log (B3):
#   eligibility   -- exactly one side eligible, or none (-> wait)
#   conflict_rule -- both sides eligible, 458's own conflict rules settled it
#                    (Q8 s9-s10: 455/456 never settle the conflict themselves)
#   fallback      -- eligibility verdicts absent for this decision_id (staged
#                    rollout); 458 keeps its pre-eligibility logic, no inventing
ORIGIN_ELIGIBILITY = "eligibility"
ORIGIN_CONFLICT_RULE = "conflict_rule"
ORIGIN_FALLBACK = "fallback"

REASON_RESOLVED = "RESOLVED"
REASON_CONFLICT = "RESOLVED_WITH_CONFLICT"
REASON_BALANCED = "BALANCED_NO_EDGE"
REASON_NO_EVIDENCE = "NO_ELIGIBLE_EVIDENCE"
REASON_SIDE_BUY = "ELIGIBLE_SIDE_BUY"
REASON_SIDE_SELL = "ELIGIBLE_SIDE_SELL"
REASON_NO_SIDE = "NO_ELIGIBLE_SIDE"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"

# B1/B2: identity crosses this hop complete. 458 used to drop account_id,
# broker and period_start (measured in the scan) and to invent decision_id
# from cycle_id -- now the six fields are read from the input, republished
# as-is, and a missing field is republished None (never invented) under the
# "identity_incomplete" warning with its name.
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "timeframe",
                   "period_start", "decision_id")
WARN_IDENTITY_INCOMPLETE = "identity_incomplete"
WARN_ELIGIBILITY_MISSING = "eligibility_missing"

_ELIGIBILITY_CAP = 256


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result == result else fallback


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


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._neutral_band = 0.05
        self._conflict_ratio = 0.5
        self._seen = 0
        self._emitted = 0
        self._counts = {DIR_BUY: 0, DIR_SELL: 0, DIR_NEUTRAL: 0, DIR_WAIT: 0}
        self._side_counts = {SIDE_BUY: 0, SIDE_SELL: 0, SIDE_WAIT: 0}
        self._conflicts = 0
        self._last_resolution = "-"
        self._dials_applied = 0
        # decision_id -> {"buy"|"sell"|"wait": latest verdict payload}
        self._eligibility: dict[str, dict[str, dict[str, Any]]] = {}
        self._eligibility_received = 0
        self._eligibility_invalid = 0
        self._origin_counts = {ORIGIN_ELIGIBILITY: 0, ORIGIN_CONFLICT_RULE: 0,
                               ORIGIN_FALLBACK: 0}
        # v3.1.0 (verdict-driven settlement): scored material parked per
        # decision_id until the eligibility trio (buy+sell+wait) for the SAME
        # id has landed -- the completing verdict settles, never the dispatch
        # race (measured live before: org=fallback whenever the settle task
        # won the gather race against the checkers' verdict deliveries).
        self._pending: dict[str, dict[str, Any]] = {}
        # (account, broker, symbol, timeframe) -> decision_id of the parked cycle
        self._pending_scope: dict[tuple[str, str, str, str], str] = {}
        self._flushed_stale = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._neutral_band = effective_value(
            "DECISION_NEUTRAL_BAND", float(context.config["neutral_band"]))
        self._conflict_ratio = effective_value(
            "DECISION_CONFLICT_RATIO", float(context.config["conflict_ratio"]))
        context.subscribe(EVENT_IN, self._on_scored)
        context.subscribe(EVENT_DIALS_COMMAND, self._on_dial_command)
        context.subscribe(EVENT_ELIGIBILITY_BUY, self._on_eligibility_buy)
        context.subscribe(EVENT_ELIGIBILITY_SELL, self._on_eligibility_sell)
        context.subscribe(EVENT_WAIT_STATE, self._on_wait_state)

    async def _on_dial_command(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        applied = apply_command(payload, atom_id="458")
        if applied is None:
            return
        attr = {"DECISION_NEUTRAL_BAND": "_neutral_band",
                "DECISION_CONFLICT_RATIO": "_conflict_ratio"}[applied["name"]]
        setattr(self, attr, float(applied["value"]))
        self._dials_applied += 1
        await self._publish_dials_state()

    async def _publish_dials_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_DIALS_STATE, {
            "id": "decision_dials_458", "atom_id": "458", "status": STATUS_OK,
            "dials": {"DECISION_NEUTRAL_BAND": self._neutral_band,
                      "DECISION_CONFLICT_RATIO": self._conflict_ratio}})

    async def start(self) -> None:
        self._running = True
        await self._publish_dials_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _store_eligibility(self, side: str, payload: dict[str, Any]) -> str:
        if not self._running or not isinstance(payload, dict):
            return ""
        decision_id = str(payload.get("decision_id") or "").strip()
        if not decision_id:
            # A verdict with no decision identity cannot be joined to any
            # decision -- counted, never guessed onto one (B1).
            self._eligibility_invalid += 1
            return ""
        slot = self._eligibility.get(decision_id)
        if slot is None:
            while len(self._eligibility) >= _ELIGIBILITY_CAP:
                self._eligibility.pop(next(iter(self._eligibility)))
            slot = self._eligibility.setdefault(decision_id, {})
        slot[side] = dict(payload)
        self._eligibility_received += 1
        return decision_id

    def _trio_complete(self, decision_id: str) -> bool:
        # The paper's literal sequence (453 -> the checkers -> 458): the
        # settlement material is ripe when all THREE verdicts -- 455 buy,
        # 456 sell, 457 wait -- for this decision_id have landed.
        states = self._eligibility.get(decision_id)
        return states is not None and all(
            side in states for side in (SIDE_BUY, SIDE_SELL, SIDE_WAIT))

    def _drop_pending(self, decision_id: str) -> dict[str, Any] | None:
        material = self._pending.pop(decision_id, None)
        for scope, pending_id in list(self._pending_scope.items()):
            if pending_id == decision_id:
                self._pending_scope.pop(scope, None)
        return material

    async def _absorb_verdict(self, side: str, payload: dict[str, Any]) -> None:
        decision_id = self._store_eligibility(side, payload)
        if not decision_id or decision_id not in self._pending:
            return
        # v3.1.0: the COMPLETING verdict -- not the dispatch race -- settles
        # the parked scored material (origin=eligibility/conflict_rule as
        # declared by the settlement body itself).
        if self._trio_complete(decision_id):
            material = self._drop_pending(decision_id)
            if material is not None:
                await self._settle(material)

    async def _on_eligibility_buy(self, payload: dict[str, Any]) -> None:
        await self._absorb_verdict(SIDE_BUY, payload)

    async def _on_eligibility_sell(self, payload: dict[str, Any]) -> None:
        await self._absorb_verdict(SIDE_SELL, payload)

    async def _on_wait_state(self, payload: dict[str, Any]) -> None:
        await self._absorb_verdict(SIDE_WAIT, payload)

    async def _on_scored(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        self._seen += 1
        # v3.1.0 -- verdict-driven settlement (the paper's literal sequence
        # 453 -> the checkers -> 458). core/event_bus.py dispatches every
        # subscriber of decision.scored.state concurrently (asyncio.gather),
        # so racing the checkers with a cooperative yield lost sometimes
        # (measured live: org=fallback while 455/456/457 were up). Now the
        # scored material settles when the eligibility trio for the SAME
        # decision_id is complete -- immediately if the verdicts already
        # landed, otherwise on the completing verdict's arrival.
        decision_id = str(payload.get("decision_id") or "").strip()
        if not decision_id:
            # No decision identity -> no verdict can ever join (B1); the
            # declared fallback settles immediately, unchanged.
            await self._settle(payload)
            return
        scope = (str(payload.get("account_id") or ""),
                 str(payload.get("broker") or ""), symbol,
                 str(payload.get("timeframe") or ""))
        # Flush-oldest-by-newest (150's sealed "newer_tick" pattern: the
        # successor event IS the deadline -- no timer, no invented number).
        # A scored cycle arriving for the same scope supersedes the still
        # unsettled previous cycle, which settles NOW through the same
        # declared body: its trio never completed, so the existing fallback
        # logic and the eligibility_missing warning apply unchanged.
        previous_id = self._pending_scope.get(scope)
        if previous_id and previous_id != decision_id:
            stale = self._drop_pending(previous_id)
            if stale is not None:
                self._flushed_stale += 1
                await self._settle(stale)
        if self._trio_complete(decision_id):
            await self._settle(payload)
            return
        # Park until the trio lands. Same existing 256 capacity as the
        # verdict store; an overflowing oldest is settled through the same
        # declared body (never silently dropped) -- every scored cycle
        # resolves exactly once.
        while len(self._pending) >= _ELIGIBILITY_CAP:
            oldest = self._drop_pending(next(iter(self._pending)))
            if oldest is not None:
                self._flushed_stale += 1
                await self._settle(oldest)
        self._pending[decision_id] = dict(payload)
        self._pending_scope[scope] = decision_id

    async def _settle(self, payload: dict[str, Any]) -> None:
        if self._context is None:
            return
        symbol = str(payload.get("symbol") or "")
        buy = _number(payload.get("buy_total"))
        sell = _number(payload.get("sell_total"))
        available = _number(payload.get("weight_available"))
        spoken = _number(payload.get("weight_spoken"))
        score = _number(payload.get("score"))
        confidence = _number(payload.get("confidence"))
        strength = _number(payload.get("strength"))
        net = buy - sell
        winner = max(buy, sell)
        loser = min(buy, sell)
        conflict = winner > 0 and (loser / winner) >= self._conflict_ratio
        if available <= 0 or spoken <= 0:
            direction, rule_reason = DIR_WAIT, REASON_NO_EVIDENCE
        elif abs(net) / available < self._neutral_band:
            direction, rule_reason = DIR_NEUTRAL, REASON_BALANCED
        else:
            direction = DIR_BUY if net > 0 else DIR_SELL
            rule_reason = REASON_CONFLICT if conflict else REASON_RESOLVED
        if direction in (DIR_WAIT, DIR_NEUTRAL):
            score = 0.0
            strength = 0.0
        self._counts[direction] += 1
        if conflict:
            self._conflicts += 1
        identity, identity_missing = _identity_of(payload)
        decision_id = identity["decision_id"]
        states = self._eligibility.get(str(decision_id)) if decision_id else None
        buy_state = states.get(SIDE_BUY) if states else None
        sell_state = states.get(SIDE_SELL) if states else None
        wait_state = states.get(SIDE_WAIT) if states else None
        eligibility_missing = [side for side, state in
                               ((SIDE_BUY, buy_state), (SIDE_SELL, sell_state))
                               if state is None]
        buy_ok = buy_state is not None and str(buy_state.get("status")) == STATUS_ELIGIBLE
        sell_ok = sell_state is not None and str(sell_state.get("status")) == STATUS_ELIGIBLE
        if eligibility_missing:
            # Staged rollout (B3): absent verdicts are never invented -- the
            # pre-eligibility logic settles, declared as such.
            origin = ORIGIN_FALLBACK
            decision_side = _RULE_TO_SIDE[direction]
            reason = rule_reason
        elif buy_ok and sell_ok:
            # Q8 s9-s10: both sides eligible is a conflict; 458's own approved
            # conflict rules settle it -- 455/456 never settle it themselves.
            origin = ORIGIN_CONFLICT_RULE
            decision_side = _RULE_TO_SIDE[direction]
            reason = rule_reason
        elif buy_ok or sell_ok:
            origin = ORIGIN_ELIGIBILITY
            decision_side = SIDE_BUY if buy_ok else SIDE_SELL
            reason = REASON_SIDE_BUY if buy_ok else REASON_SIDE_SELL
        else:
            origin = ORIGIN_ELIGIBILITY
            decision_side = SIDE_WAIT
            reason = REASON_NO_SIDE
        self._side_counts[decision_side] += 1
        self._origin_counts[origin] += 1
        resolution = {
            "origin": origin,
            "rule_direction": direction, "rule_reason": rule_reason,
            "buy_status": buy_state.get("status") if buy_state else None,
            "sell_status": sell_state.get("status") if sell_state else None,
            "wait_status": wait_state.get("status") if wait_state else None,
            "wait_reason": wait_state.get("reason") if wait_state else None,
            "eligibility_missing": eligibility_missing,
            "eligibility_conflict": bool(not eligibility_missing and buy_ok and sell_ok),
        }
        warnings: list[str] = []
        if decision_side not in (SIDE_BUY, SIDE_SELL):
            warnings.append(reason)
        if identity_missing:
            warnings.append(WARN_IDENTITY_INCOMPLETE)
        if eligibility_missing:
            warnings.append(WARN_ELIGIBILITY_MISSING)
        if available > 0 and spoken > 0:
            self._last_resolution = "%s ratio=%.3f/band=%.2f net=%.3f avail=%.2f org=%s" % (
                decision_side, abs(net) / available, self._neutral_band, net,
                available, origin)
        else:
            self._last_resolution = "%s (%s) org=%s" % (decision_side, reason, origin)
        cycle_id = str(payload.get("cycle_id") or "")
        await self._context.publish(EVENT_OUT, {
            **identity, "symbol": symbol, "id": ID_RESOLVE, "cycle_id": cycle_id,
            "identity_missing": identity_missing,
            "status": STATUS_OK,
            # Legacy word fields kept as-is for existing consumers (360/463/581):
            # they mirror the pre-eligibility rule verdict. The settled side of
            # the decision lives ONLY in decision_side (B6).
            "signal": direction, "direction": direction,
            "decision_side": decision_side, "resolution": resolution,
            "score": round(score, 2), "confidence": round(confidence, 6),
            "strength": round(strength, 6), "reason": reason, "conflict": conflict,
            "quality": QUALITY_GOOD if decision_side in (SIDE_BUY, SIDE_SELL) else QUALITY_LOW,
            "warnings": warnings,
            "buy_total": round(buy, 6), "sell_total": round(sell, 6),
            "net": round(net, 6), "weight_available": round(available, 6),
            "weight_spoken": round(spoken, 6),
            "contributions": payload.get("contributions"),
            "evidence": payload.get("evidence"),
            "metadata": {"neutral_band": self._neutral_band,
                         "conflict_ratio": self._conflict_ratio}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "emitted": self._emitted,
                   "conflicts": self._conflicts, "directions": dict(self._counts),
                   "sides": dict(self._side_counts),
                   "origins": dict(self._origin_counts),
                   "eligibility_received": self._eligibility_received,
                   "eligibility_invalid": self._eligibility_invalid,
                   "eligibility_tracked": len(self._eligibility),
                   "pending_scored": len(self._pending),
                   "flushed_stale": self._flushed_stale,
                   "last_resolution": self._last_resolution,
                   "dials": {"neutral_band": self._neutral_band,
                             "conflict_ratio": self._conflict_ratio,
                             "applied": self._dials_applied}}
        if not self._seen:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="buy=%d sell=%d wait=%d conflicts=%d elig=%d last=%s" % (
                                self._side_counts[SIDE_BUY], self._side_counts[SIDE_SELL],
                                self._side_counts[SIDE_WAIT],
                                self._conflicts, self._eligibility_received,
                                self._last_resolution),
                            details=details)
