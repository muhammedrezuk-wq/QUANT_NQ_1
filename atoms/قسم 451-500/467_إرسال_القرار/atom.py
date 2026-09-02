from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.2.0"

EVENT_IN = "decision.approved.state"
EVENT_PULSE = "SYS_SECOND"
EVENT_OUT = "decision.dispatch.state"
EVENT_GATE_PASSED = "decision.gate.passed"
EVENT_GATE_BLOCKED = "decision.gate.blocked"
EVENT_GATE_RECORDED = "decision.gate.recorded"

METHOD = "decision_to_execution_gate"
ID_DISPATCH = "decision_dispatch"

DIR_BUY = "buy"
DIR_SELL = "sell"
DIR_WAIT = "wait"

STATE_PASSED = "PASSED"
STATE_BLOCKED = "BLOCKED"
STATE_RECORDED = "RECORDED"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"
REASON_NOT_APPROVED = "NOT_APPROVED"
REASON_SIDE_UNKNOWN = "SIDE_UNKNOWN"
REASON_NO_IDENTITY = "NO_DECISION_IDENTITY"
RESTORE_INVALID = "RESTORE_INVALID_STARTED_EMPTY"

WARNING_IDENTITY = "identity_incomplete"

REQUEST_TAG = ":req"
# The six identity fields of the decision contract (owner papers 2026-08-20):
# account / broker / symbol / timeframe / cycle start / decision id.
# v2.2.0 (nq seal 2026-08-25): aligned to the SAME six fields 452/453/454/466
# measure (period_start, not cycle_id) -- 467 was the lone deviator, so
# identity_incomplete was computed on two different bases across the chain
# and could not be trusted (dashboard-model finding, ruled under the owner's
# delegation). cycle_id stays in the published payload as information.
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "timeframe",
                   "period_start", "decision_id")

_SIDE = {DIR_BUY: "BUY", DIR_SELL: "SELL"}
# Dedup memory bound only (not a trading value): oldest tracked decision id is
# evicted beyond this count so the map cannot grow without limit.
_MAX_TRACKED_DECISIONS = 4096


def _meta(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _approved_flag(payload: dict[str, Any]) -> Any:
    flag = payload.get("approved")
    if isinstance(flag, bool):
        return flag
    return _meta(payload).get("approved")


def _side_text(payload: dict[str, Any]) -> str:
    raw = (payload.get("decision_side") or _meta(payload).get("direction")
           or payload.get("signal") or "")
    return str(raw).strip().lower()


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._clock: float | None = None
        self._attempts: dict[str, int] = {}
        self._seen = 0
        self._passed = 0
        self._blocked = 0
        self._recorded = 0
        self._duplicates = 0
        self._identity_incomplete = 0
        self._restore_note = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_approved)
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        try:
            self._clock = float(payload["official_time"])
        except (KeyError, TypeError, ValueError):
            return

    def _remember(self, base: str) -> int:
        count = self._attempts.get(base, 0) + 1
        if base not in self._attempts and len(self._attempts) >= _MAX_TRACKED_DECISIONS:
            self._attempts.pop(next(iter(self._attempts)))
        self._attempts[base] = count
        return count

    async def _emit(self, event: str, body: dict[str, Any]) -> None:
        if self._context is not None:
            await self._context.publish(event, body)

    async def _on_approved(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._seen += 1
        side = _side_text(payload)
        approved = _approved_flag(payload)
        decision_id = str(payload.get("decision_id") or "")
        cycle_id = str(payload.get("cycle_id") or "")
        base = decision_id or cycle_id
        missing = [field for field in IDENTITY_FIELDS if not payload.get(field)]
        body: dict[str, Any] = dict(payload)
        body.update({"decision_side": side, "gated_at": self._clock,
                     "gate_source": ID_DISPATCH, "gate_warnings": []})
        if missing:
            self._identity_incomplete += 1
            body["gate_warnings"] = [WARNING_IDENTITY]
            body["identity_missing"] = missing
        if side == DIR_WAIT:
            self._recorded += 1
            body["gate_state"] = STATE_RECORDED
            await self._emit(EVENT_GATE_RECORDED, body)
            return
        reject = ""
        if approved is not True:
            reject = str(_meta(payload).get("reason") or payload.get("reason")
                         or REASON_NOT_APPROVED)
        elif side not in _SIDE:
            reject = REASON_SIDE_UNKNOWN
        elif not base:
            reject = REASON_NO_IDENTITY
        if reject:
            self._blocked += 1
            body.update({"gate_state": STATE_BLOCKED, "reject_reason": reject})
            await self._emit(EVENT_GATE_BLOCKED, body)
            return
        attempts = self._attempts.get(base, 0)
        redispatch = str(payload.get("redispatch_reason") or "").strip()
        if attempts and not redispatch:
            self._duplicates += 1
            return
        request_id = "%s%s%d" % (base, REQUEST_TAG, self._remember(base))
        body.update({"gate_state": STATE_PASSED, "gate_request_id": request_id})
        self._passed += 1
        await self._emit(EVENT_GATE_PASSED, body)
        await self._emit(EVENT_OUT, {
            "request_id": request_id, "gate_request_id": request_id,
            "decision_id": decision_id, "cycle_id": cycle_id,
            "account_id": payload.get("account_id"), "symbol": payload.get("symbol"),
            "side": _SIDE[side], "approved": True, "source": ID_DISPATCH,
            "executable": True, "gated_at": self._clock})

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "attempts": dict(self._attempts)}

    async def restore(self, state: dict[str, Any]) -> None:
        rows = state.get("attempts") if isinstance(state, dict) else None
        if not isinstance(rows, dict):
            self._attempts = {}
            self._restore_note = RESTORE_INVALID
            return
        cleaned: dict[str, int] = {}
        for base, count in rows.items():
            try:
                parsed = int(count)
            except (TypeError, ValueError):
                continue
            if isinstance(base, str) and base and parsed > 0:
                cleaned[base] = parsed
        self._attempts = cleaned
        self._restore_note = ""

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "passed": self._passed,
                   "blocked": self._blocked, "recorded": self._recorded,
                   "duplicates": self._duplicates,
                   "identity_incomplete": self._identity_incomplete,
                   "tracked_decisions": len(self._attempts)}
        if self._restore_note:
            details["restore_note"] = self._restore_note
        if self._seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d passed=%d blocked=%d recorded=%d duplicates=%d" % (
                self._seen, self._passed, self._blocked, self._recorded,
                self._duplicates),
            details=details)
