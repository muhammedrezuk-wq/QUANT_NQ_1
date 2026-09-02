from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.section_live import section_live

ATOM_VERSION = "1.6.0"

# 1.6.0 (2026-08-25): panel emission PACED (once per second per scope; the
# SYS_SECOND pulse flushes dirty scopes) -- building the full units panel on
# every unit delivery drowned the loop under the freed feed (measured on
# 350/400: the 30s handler isolation tripped every cycle).
_PANEL_MIN_INTERVAL_S = 1.0

EVENT_TIME = "SYS_SECOND"
EVENT_OUT = "liquidity.cycle.collected"
EVENT_LIVE = "liquidity.section.live"
# Section 250 closure, atoms-first (owner order 2026-08-23): the units panel --
# every liquidity unit declares ITSELF: what it last said, its own rhythm, its
# delivery count and age. Sourceless units are DECLARED excluded, never hidden.
EVENT_PANEL = "liquidity.units.state"

# Owner ruling 2026-08-23 (D10): delta / cvd / absorption have NO source at
# the broker -- no trade tape exists to feed them. They stay dormant by owner
# order and are excluded from the section's expectation. Waking them later =
# the owner gains a tape source, then re-adds the events here + a version
# bump. Until then the section completes from sourced units only.
_SOURCELESS_EXCLUDED = (
    "liquidity.delta.state",
    "liquidity.cvd.state",
    "liquidity.absorption.state",
)
_SOURCELESS_IDS = tuple(
    event.replace("liquidity.", "").replace(".state", "")
    for event in _SOURCELESS_EXCLUDED
)
_SOURCELESS_REASON = "NO_SOURCE_AT_BROKER"

_UNIT_EVENTS = (
    "liquidity.pool.state",
    "liquidity.buyside.state",
    "liquidity.sellside.state",
    "liquidity.sweep.state",
    "liquidity.fvg.state",
    # Campaign 1-449 batch A (2026-08-23): depth HAS a live source (106/622)
    # -- unlike the tape-based trio excluded by owner ruling D10.
    "liquidity.depth.state",
)
_UNIT_IDS = tuple(
    event.replace("liquidity.", "").replace(".state", "")
    for event in _UNIT_EVENTS
)
_ALL_IDS = _UNIT_IDS + _SOURCELESS_IDS

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CYCLES = "NO_CYCLES_YET"

_RECENT_CAP = 128

_TIMEFRAME_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _timeframe_seconds(text_value: Any) -> float | None:
    text = str(text_value or "").strip().lower()
    if len(text) < 2 or text[-1] not in _TIMEFRAME_UNITS:
        return None
    try:
        amount = float(text[:-1])
    except ValueError:
        return None
    return amount * _TIMEFRAME_UNITS[text[-1]]


@section_atom("250", "250")
@section_live("250", EVENT_LIVE)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._expected = len(_UNIT_EVENTS)
        self._timeout_s = 5.0
        self._now = 0.0
        self._cycles: dict[str, dict[str, Any]] = {}
        self._recent: deque = deque(maxlen=_RECENT_CAP)
        self._opened = 0
        self._forwarded = 0
        self._late = 0
        self._invalid = 0
        # Section 250 closure: per-unit self-declared registry.
        self._unit_latest: dict[tuple[tuple[str, str, str, str], str], dict[str, Any]] = {}
        self._unit_deliveries: dict[tuple[tuple[str, str, str, str], str], int] = {}
        self._panel_emitted = 0
        self._panel_last = {}
        self._panel_dirty = set()

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeout_s = float(context.config["timeout_seconds"])
        # Section 250 closure (owner vision, atoms-first 2026-08-23): same
        # cure as 200 -- no more per-tick empty cycles. A cycle opens from the
        # FIRST unit delivery under the unit's OWN identity and completes
        # naturally. Sourceless units (owner ruling D10) are excluded from the
        # expectation and declared -- the section waits for nobody.
        context.subscribe(EVENT_TIME, self._on_time)
        for event in _UNIT_EVENTS:
            context.subscribe(event, self._on_unit_state)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_unit_state(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        cycle_id = str(payload.get("cycle_id") or "")
        unit_id = str(payload.get("id") or "")
        symbol = str(payload.get("symbol") or "")
        account = str(payload.get("account_id") or "")
        broker = str(payload.get("broker") or "")
        timeframe = str(payload.get("timeframe") or "")
        if not cycle_id or not unit_id or not symbol:
            self._invalid += 1
            return
        scope = (account, broker, symbol, timeframe)
        declared = {
            "direction": _to_float(payload.get("direction")),
            "strength": _to_float(payload.get("strength")),
            "confidence": _to_float(payload.get("confidence")),
            "weight": _to_float(payload.get("weight")),
            "ready": payload.get("ready") is True,
            "state": str(payload.get("state") or payload.get("analysis_state") or ""),
            "timeframe": timeframe,
            "period_start": payload.get("period_start"),
            "cycle_id": cycle_id,
            "delivered_wall": self._now,
        }
        self._unit_latest[(scope, unit_id)] = declared
        key = (scope, unit_id)
        self._unit_deliveries[key] = self._unit_deliveries.get(key, 0) + 1
        await self._emit_panel(scope)
        cycle = self._cycles.get(cycle_id)
        if cycle is None:
            if cycle_id in self._recent:
                self._late += 1
                return
            self._cycles[cycle_id] = {
                "account_id": account, "broker": broker,
                "symbol": symbol, "timeframe": timeframe,
                "open_time": self._now, "results": {}}
            self._opened += 1
            cycle = self._cycles[cycle_id]
        cycle["results"][unit_id] = payload
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
        # paced panels: the SYS_SECOND pulse IS the pace, so it forces.
        for scope in list(self._panel_dirty):
            await self._emit_panel(scope, force=True)

    async def _forward(self, cycle_id: str) -> None:
        cycle = self._cycles.pop(cycle_id, None)
        if cycle is None or self._context is None:
            return
        self._recent.append(cycle_id)
        account = str(cycle.get("account_id") or "")
        broker = str(cycle.get("broker") or "")
        present = len(cycle["results"])
        results = cycle["results"]
        ready_ids = {k for k, v in results.items()
                     if isinstance(v, dict) and v.get("ready") is True}
        available_weight = sum(float(v.get("weight", 0.0)) for v in results.values()
                               if isinstance(v, dict))
        active_weight = sum(float(v.get("weight", 0.0)) for v in results.values()
                            if isinstance(v, dict) and v.get("ready") is True)
        ratios = ({k: round(float(v.get("weight", 0.0)) / available_weight * 100.0, 4)
                   for k, v in results.items() if isinstance(v, dict)}
                  if available_weight > 0 else {})
        collected = {
            "cycle_id": cycle_id, "account_id": account or None,
            "broker": broker or None,
            "symbol": cycle["symbol"], "timeframe": cycle["timeframe"],
            "results": results, "expected": self._expected, "present": present,
            "complete": present >= self._expected,
            "active_weight": round(active_weight, 4),
            "available_weight": round(available_weight, 4),
            "missing_weight": round(max(0.0, available_weight - active_weight), 4),
            "ready_units": sorted(ready_ids), "ratios": ratios,
            "excluded_units": list(_SOURCELESS_IDS),
            "unreported_units": [u for u in _UNIT_IDS if u not in results]}
        self._live_section.observe_cycle(collected)
        await self._context.publish(EVENT_OUT, collected)
        self._forwarded += 1

    async def _emit_panel(self, scope: tuple[str, str, str, str],
                          force: bool = False) -> None:
        # Section 250 closure: one event, one row per unit. Sourced units
        # declare themselves; sourceless units are declared EXCLUDED by owner
        # ruling -- visible, not hidden, never waited for.
        if self._context is None:
            return
        # paced: a burst marks the scope dirty; SYS_SECOND flushes it
        # with force=True (the pulse is already the pace).
        mono = time.monotonic()
        if not force and mono - self._panel_last.get(scope, 0.0) < _PANEL_MIN_INTERVAL_S:
            self._panel_dirty.add(scope)
            return
        self._panel_last[scope] = mono
        self._panel_dirty.discard(scope)
        account, broker, symbol, timeframe = scope
        rows = []
        for unit_id in _UNIT_IDS:
            declared = self._unit_latest.get((scope, unit_id))
            if declared is None:
                rows.append({"id": unit_id, "present": False})
                continue
            next_expected = None
            seconds = _timeframe_seconds(declared.get("timeframe"))
            start = _to_float(declared.get("period_start"))
            if seconds is not None and start is not None:
                next_expected = start + seconds
            rows.append({"id": unit_id, "present": True,
                         "direction": declared.get("direction"),
                         "strength": declared.get("strength"),
                         "confidence": declared.get("confidence"),
                         "weight": declared.get("weight"),
                         "ready": declared.get("ready"),
                         "state": declared.get("state"),
                         "timeframe": declared.get("timeframe"),
                         "period_start": declared.get("period_start"),
                         "cycle_id": declared.get("cycle_id"),
                         "deliveries": self._unit_deliveries.get((scope, unit_id), 0),
                         "delivered_at": (declared.get("delivered_wall")
                                          if declared.get("delivered_wall") is not None else None),
                         "age_s": (round(self._now - declared["delivered_wall"], 1)
                                   if declared.get("delivered_wall") is not None else None),
                         "next_expected_at": next_expected})
        for unit_id in _SOURCELESS_IDS:
            rows.append({"id": unit_id, "present": False,
                         "excluded": True, "reason": _SOURCELESS_REASON})
        present = sum(1 for row in rows if row.get("present"))
        await self._context.publish(EVENT_PANEL, {
            "account_id": account or None, "broker": broker or None,
            "symbol": symbol, "timeframe": timeframe or None,
            "units": rows, "expected": self._expected, "present": present,
            "excluded_units": list(_SOURCELESS_IDS),
            "missing": [row["id"] for row in rows
                        if not row.get("present") and not row.get("excluded")]})
        self._panel_emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"opened": self._opened, "forwarded": self._forwarded,
                   "open": len(self._cycles), "expected": self._expected,
                   "late": self._late, "invalid": self._invalid,
                   "units_tracked": len(self._unit_latest),
                   "panel_emitted": self._panel_emitted,
                   "excluded_units": list(_SOURCELESS_IDS)}
        if self._opened == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CYCLES,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="opened=%d forwarded=%d open=%d late=%d units=%d panel=%d excluded=%d" % (
                self._opened, self._forwarded, len(self._cycles), self._late,
                len(self._unit_latest), self._panel_emitted, len(_SOURCELESS_IDS)),
            details=details)
