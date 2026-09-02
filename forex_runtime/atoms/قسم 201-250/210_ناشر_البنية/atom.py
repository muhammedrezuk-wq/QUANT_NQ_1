from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.section_live import REQUIRED_UNITS

ATOM_VERSION = "1.3.0"

EVENT_IN = "structure.cycle.validated"
EVENT_OUT = "market.structure.updated"

ID_STRUCTURE = "structure"

UID_TREND = "structure_trend"
UID_PHASE = "phase"
UID_SWING = "swing"
UID_EXTERNAL = "external"
UID_INTERNAL = "internal"
UID_MSS = "mss"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

DEFAULT_TREND = "range"
DEFAULT_PHASE = "neutral"
DEFAULT_NONE = "none"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NOTHING = "NOTHING_PUBLISHED_YET"

REQUIRED_DEPTH = 100.0

REQUIRED_UNIT_IDS: frozenset[str] = REQUIRED_UNITS.get("200", frozenset())


def _ready_required(results: dict[str, Any]) -> list[str]:
    return sorted(
        uid for uid in REQUIRED_UNIT_IDS
        if isinstance(results.get(uid), dict) and results[uid].get("status") == STATUS_OK)


# Owner stamp 2026-08-25: a unit whose confidence is unreadable used to be
# skipped in silence -- no counter, no health field. If every unit were
# skipped the section published confidence = None with nothing saying why.
# The count now rides back with the value so the caller can declare it.
def _section_confidence(results: dict[str, Any],
                        ready_required: list[str]) -> tuple[float | None, int]:
    values = []
    unreadable = 0
    for uid in ready_required:
        raw = results[uid].get("confidence")
        if isinstance(raw, bool) or raw is None:
            unreadable += 1
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            unreadable += 1
            continue
    return (sum(values) / len(values) if values else None), unreadable


def _signal(results: dict[str, Any], uid: str, default: str) -> Any:
    state = results.get(uid)
    return state.get("signal", default) if isinstance(state, dict) else default


def _meta(results: dict[str, Any], uid: str, field: str) -> Any:
    state = results.get(uid)
    if isinstance(state, dict):
        meta = state.get("metadata") or {}
        return meta.get(field)
    return None


@section_atom("200", "210")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._seen = 0
        self._published = 0
        self._unreadable_confidence = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_validated)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_validated(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        self._seen += 1
        cycle_id = str(payload.get("cycle_id", ""))
        timeframe = str(payload.get("timeframe", ""))
        results = payload.get("results") or {}
        trend_state = results.get(UID_TREND) if isinstance(results.get(UID_TREND), dict) else {}
        ready_required = _ready_required(results)
        current_depth = (100.0 * len(ready_required) / len(REQUIRED_UNIT_IDS)
                          if REQUIRED_UNIT_IDS else 0.0)
        complete = bool(REQUIRED_UNIT_IDS) and len(ready_required) >= len(REQUIRED_UNIT_IDS)
        status = STATUS_OK if complete else STATUS_INSUFFICIENT
        quality = QUALITY_GOOD if complete else QUALITY_LOW
        structure = {
            "trend": _signal(results, UID_TREND, DEFAULT_TREND),
            "phase": _signal(results, UID_PHASE, DEFAULT_PHASE),
            "swing": _signal(results, UID_SWING, DEFAULT_NONE),
            "swing_price": _meta(results, UID_SWING, "price"),
            "external_high": _meta(results, UID_EXTERNAL, "swing_high"),
            "external_low": _meta(results, UID_EXTERNAL, "swing_low"),
            "internal": _signal(results, UID_INTERNAL, DEFAULT_NONE),
            "last_shift": {"type": _meta(results, UID_MSS, "shift_type"),
                           "direction": _meta(results, UID_MSS, "direction")}}
        meta = {"timeframe": timeframe, "present": payload.get("present", 0),
                "expected": payload.get("expected", 0)}
        confidence, unreadable = _section_confidence(results, ready_required)
        self._unreadable_confidence += unreadable
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_STRUCTURE, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "account_id": payload.get("account_id"),
            "broker": payload.get("broker"),
            "status": status, "signal": structure["trend"],
            # NQ seal item 22 (A8): the section's direction is its trend WORD
            # (uptrend/downtrend/range/transition) -- lifted as-is under
            # direction_word. No word->number conversion is invented here,
            # and no numeric direction or strength exists at this publisher.
            "direction_word": structure["trend"],
            "score": int(trend_state.get("score", 0)),
            "confidence": confidence,
            "current_depth": round(current_depth, 4),
            "required_depth": REQUIRED_DEPTH,
            "complete": complete,
            "quality": quality, "warnings": [],
            "structure": structure, "metadata": meta})
        self._published += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._published == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NOTHING)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d published=%d unreadable_conf=%d" % (
                self._seen, self._published, self._unreadable_confidence),
            details={"seen": self._seen, "published": self._published,
                     "unreadable_confidence": self._unreadable_confidence})
