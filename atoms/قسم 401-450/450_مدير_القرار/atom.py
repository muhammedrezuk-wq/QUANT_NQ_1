"""Decision cycle manager: supervises the single decision path and keeps
its unified record.

NQ seal item 22, batch B (B2 per paper Q8): 450 manages the decision cycle
-- it NEVER invents a decision. Two duties:

1. Cycle collection (existing surface, kept): gathers the per-cycle unit
   states and publishes decision.cycle.collected (the governance dashboard
   feeds on it). The buy/sell units are now the eligibility checkers'
   states (decision.eligibility.buy.state / decision.eligibility.sell.state)
   instead of the removed parallel-decision events.

2. Unified decision record (new, Q8 section 23): for every decision it
   builds ONE card -- decision.cycle.record -- holding {final decision when
   it arrives (from 458's resolution -- 450 takes it, never makes it), buy
   eligibility and its reason, sell eligibility and its reason, wait state
   and its reason, the six-part identity, and the times of every stage}.
   Display and audit only: no decision logic lives here.

Identity is carried as published upstream; a missing decision_id in the
staged rollout stays None and is declared with the "identity_incomplete"
warning -- never invented here (the record correlates by cycle_id then).
"""
from __future__ import annotations

from collections import deque
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "1.2.1"

EVENT_IN = "market.tick.validated"
EVENT_TIME = "SYS_SECOND"
EVENT_OUT = "decision.cycle.collected"
EVENT_RECORD = "decision.cycle.record"

EVENT_BUY = "decision.eligibility.buy.state"
EVENT_SELL = "decision.eligibility.sell.state"
EVENT_WAIT = "decision.wait.state"
EVENT_RESOLVED = "decision.resolved.state"

#: Unit states collected into decision.cycle.collected (legacy surface).
_UNIT_EVENTS = (
    "decision.aggregated.state",
    "decision.scored.state",
    "decision.filtered.state",
    EVENT_BUY,
    EVENT_SELL,
    EVENT_WAIT,
    "decision.approved.state",
)

# Campaign 450-901: symbol position inside the scope key tuple.
_SCOPE_SYMBOL_INDEX = 3

ID_RECORD = "decision_cycle_record"
STATUS_OK = "ok"

STAGE_BUY = "buy_eligibility"
STAGE_SELL = "sell_eligibility"
STAGE_WAIT = "wait"
STAGE_RESOLUTION = "resolution"
_STAGES = (STAGE_BUY, STAGE_SELL, STAGE_WAIT, STAGE_RESOLUTION)

WARN_IDENTITY_INCOMPLETE = "identity_incomplete"

IDENTITY_FIELDS = ("account_id", "broker", "symbol", "timeframe",
                   "period_start", "decision_id")

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CYCLES = "NO_CYCLES_YET"

_RECENT_CAP = 128
_RECORDS_CAP = 256


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._expected = len(_UNIT_EVENTS)
        self._timeout_s = 5.0
        self._now = 0.0
        self._cycles: dict[str, dict[str, Any]] = {}
        self._key_open: dict[tuple, str] = {}
        self._opened = 0
        self._forwarded = 0
        self._recent: deque = deque(maxlen=_RECENT_CAP)
        self._records: dict[str, dict[str, Any]] = {}
        self._records_published = 0
        self._records_completed = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeout_s = float(context.config["timeout_seconds"])
        context.subscribe(EVENT_IN, self._on_tick)
        context.subscribe(EVENT_TIME, self._on_time)
        for event in _UNIT_EVENTS:
            if event not in (EVENT_BUY, EVENT_SELL, EVENT_WAIT):
                context.subscribe(event, self._on_unit_state)
        context.subscribe(EVENT_BUY, self._on_buy_state)
        context.subscribe(EVENT_SELL, self._on_sell_state)
        context.subscribe(EVENT_WAIT, self._on_wait_state)
        context.subscribe(EVENT_RESOLVED, self._on_resolved)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # ── cycle collection (legacy surface, kept for the dashboard) ─────────

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        account = str(payload.get("account_id") or "")
        broker = str(payload.get("broker") or "")
        sequence = str(payload.get("sequence") or "")
        timeframe = "tick"
        if not account or not broker or not sequence:
            return
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=sequence)
        key = (account, broker, symbol, timeframe)
        prev = self._key_open.get(key)
        if prev is not None and prev != cycle_id and prev in self._cycles:
            await self._forward(prev)
        if cycle_id in self._recent:
            return
        cycle = self._cycles.get(cycle_id)
        if cycle is None:
            self._cycles[cycle_id] = {"symbol": symbol, "timeframe": timeframe,
                                      "open_time": self._now, "results": {}}
            self._opened += 1
        else:
            cycle["symbol"] = symbol
            cycle["timeframe"] = timeframe
        self._key_open[key] = cycle_id

    async def _on_unit_state(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        cycle_id = payload.get("cycle_id")
        unit_id = payload.get("id")
        if not cycle_id or not unit_id:
            return
        cycle_id = str(cycle_id)
        cycle = self._cycles.get(cycle_id)
        if cycle is None:
            if cycle_id in self._recent:
                return
            cycle = {"symbol": str(payload.get("symbol", "")), "timeframe": "",
                     "open_time": self._now, "results": {}}
            self._cycles[cycle_id] = cycle
            self._opened += 1
        cycle["results"][str(unit_id)] = payload
        if len(cycle["results"]) >= self._expected:
            await self._forward(cycle_id)

    async def _on_time(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        now = _to_float(payload.get("official_time"))
        if now is None:
            return
        self._now = now
        expired = [cid for cid, c in self._cycles.items()
                   if c["open_time"] > 0.0 and (now - c["open_time"]) > self._timeout_s]
        for cid in expired:
            await self._forward(cid)

    async def _forward(self, cycle_id: str) -> None:
        cycle = self._cycles.pop(cycle_id, None)
        if cycle is None or self._context is None:
            return
        self._recent.append(cycle_id)
        # Campaign 450-901 (2026-08-23): named, not magic -- the key tuple is
        # (account, broker, symbol, ...) and the symbol sits at index 2.
        for k in list(self._key_open):
            if len(k) >= _SCOPE_SYMBOL_INDEX and k[_SCOPE_SYMBOL_INDEX] == cycle["symbol"] \
                    and self._key_open[k] == cycle_id:
                del self._key_open[k]
                break
        present = len(cycle["results"])
        await self._context.publish(EVENT_OUT, {
            "cycle_id": cycle_id, "symbol": cycle["symbol"], "timeframe": cycle["timeframe"],
            "results": cycle["results"], "expected": self._expected, "present": present,
            "complete": present >= self._expected})
        self._forwarded += 1

    # ── unified decision record (Q8 section 23 -- no decision made here) ──

    async def _on_buy_state(self, payload: dict[str, Any]) -> None:
        await self._on_unit_state(payload)
        await self._record_stage(STAGE_BUY, payload)

    async def _on_sell_state(self, payload: dict[str, Any]) -> None:
        await self._on_unit_state(payload)
        await self._record_stage(STAGE_SELL, payload)

    async def _on_wait_state(self, payload: dict[str, Any]) -> None:
        await self._on_unit_state(payload)
        await self._record_stage(STAGE_WAIT, payload)

    async def _on_resolved(self, payload: dict[str, Any]) -> None:
        # 458's resolution feeds the record only -- it is not a collected
        # unit of the legacy surface, and 450 never overrides it.
        await self._record_stage(STAGE_RESOLUTION, payload)

    def _record_for(self, key: str) -> dict[str, Any]:
        record = self._records.get(key)
        if record is None:
            if len(self._records) >= _RECORDS_CAP:
                self._records.pop(next(iter(self._records)))
            record = {"identity": {field: None for field in IDENTITY_FIELDS},
                      "cycle_id": None, "stages": {}}
            self._records[key] = record
        return record

    def _merge_identity(self, record: dict[str, Any], payload: dict[str, Any]) -> None:
        """Fill identity gaps from the arriving payload -- never overwrite a
        known value with None, never invent a missing one."""
        for field in IDENTITY_FIELDS:
            raw = payload.get(field)
            if raw in (None, ""):
                continue
            value = raw if field == "period_start" else str(raw)
            if record["identity"][field] is None:
                record["identity"][field] = value
        cycle_id = payload.get("cycle_id")
        if record["cycle_id"] is None and cycle_id not in (None, ""):
            record["cycle_id"] = str(cycle_id)

    async def _record_stage(self, stage: str, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        key = str(payload.get("decision_id") or "") or str(payload.get("cycle_id") or "")
        if not key:
            return
        record = self._record_for(key)
        self._merge_identity(record, payload)
        entry: dict[str, Any] = {
            "source_timestamp": payload.get("source_timestamp"),
            "received_at": self._now if self._now > 0 else None,
        }
        if stage == STAGE_RESOLUTION:
            entry["decision"] = payload.get("direction", payload.get("signal"))
            entry["reason"] = payload.get("reason")
            entry["conflict"] = payload.get("conflict")
        else:
            entry["status"] = payload.get("status")
            entry["reason"] = payload.get("reason")
        record["stages"][stage] = entry
        await self._publish_record(key, record)

    async def _publish_record(self, key: str, record: dict[str, Any]) -> None:
        if self._context is None:
            return
        identity = record["identity"]
        stages = record["stages"]
        resolution = stages.get(STAGE_RESOLUTION)
        buy = stages.get(STAGE_BUY)
        sell = stages.get(STAGE_SELL)
        wait = stages.get(STAGE_WAIT)
        missing = [field for field in IDENTITY_FIELDS if identity[field] is None]
        complete = all(stage in stages for stage in _STAGES)
        if complete:
            self._records_completed += 1
        await self._context.publish(EVENT_RECORD, {
            "id": ID_RECORD, "status": STATUS_OK, "record_key": key,
            "decision_id": identity["decision_id"],
            "cycle_id": record["cycle_id"],
            "account_id": identity["account_id"], "broker": identity["broker"],
            "symbol": identity["symbol"], "timeframe": identity["timeframe"],
            "period_start": identity["period_start"],
            "final_decision": resolution.get("decision") if resolution else None,
            "final_reason": resolution.get("reason") if resolution else None,
            "conflict": resolution.get("conflict") if resolution else None,
            "buy_eligibility": ({"status": buy.get("status"),
                                 "reason": buy.get("reason")} if buy else None),
            "sell_eligibility": ({"status": sell.get("status"),
                                  "reason": sell.get("reason")} if sell else None),
            "wait_state": ({"status": wait.get("status"),
                            "reason": wait.get("reason")} if wait else None),
            "stage_times": {stage: {"source_timestamp": entry.get("source_timestamp"),
                                    "received_at": entry.get("received_at")}
                            for stage, entry in stages.items()},
            "stages_present": [stage for stage in _STAGES if stage in stages],
            "complete": complete,
            "warnings": [WARN_IDENTITY_INCOMPLETE] if missing else [],
            "missing_identity": missing,
            "timestamp": clock.now()})
        self._records_published += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"opened": self._opened, "forwarded": self._forwarded,
                   "open": len(self._cycles), "expected": self._expected,
                   "records_open": len(self._records),
                   "records_published": self._records_published,
                   "records_completed": self._records_completed}
        if self._opened == 0 and not self._records:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CYCLES,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="opened=%d forwarded=%d open=%d records=%d published=%d" % (
                self._opened, self._forwarded, len(self._cycles),
                len(self._records), self._records_published),
            details=details)
