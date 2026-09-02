from __future__ import annotations

import asyncio
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.analysis_speed import speed_value
from shared.decision_dials import (EVENT_COMMAND as EVENT_SETTINGS_COMMAND,
                                   EVENT_STATE as EVENT_SETTINGS_STATE,
                                   apply_command)
from shared.live_analysis import MODE_CANDLE, MODE_LIVE, STATE_READY
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = '2.9.0'
# v2.9.0 (2026-08-27, item 10/27 of the 27-atom review -- confirmed lost
# update when two units race for the same scope): _on_live_state's
# "newer_tick" branch popped the old batch, awaited its flush (a real
# suspension point -- publish + panel emit), then UNCONDITIONALLY created
# and stored a fresh self._live_batch[scope], discarding any batch a
# concurrent unit's handler had already created there in the meantime.
# The lost unit's DATA survives (self._live_latest is untouched), but its
# membership in the batch's "arrived" set is gone -- the all-units fast
# flush can then never trigger (permanently short by however many
# memberships were clobbered), silently degrading every affected cycle to
# the 1s timeout path instead of firing the instant all analysts report.
# Fixed with a per-scope asyncio.Lock serializing the whole read-batch /
# maybe-flush / write-batch section against other same-scope deliveries
# (same shape as the 516/578 fix: the race needs an await point plus a
# blind overwrite after it, not shared mutable state alone).
EVENT_TIME = "SYS_SECOND"
EVENT_OUT = 'analysis.cycle.collected'
# Unit 150 closure, phase 3 (owner order 2026-08-23): the analysts panel --
# every analyst declares ITSELF: what it last said, its own rhythm, when it
# last delivered and (for candle rhythm) when its next delivery is expected.
# Computed nowhere downstream: the panel IS the event the dashboard reads.
EVENT_PANEL = 'analysis.analysts.state'
_UNIT_EVENTS = ('analysis.trend.state', 'analysis.momentum.state', 'analysis.volatility.state', 'analysis.volume.state', 'analysis.spread.state', 'analysis.candle.state', 'analysis.gap.state', 'analysis.session.state', 'analysis.time.state', 'analysis.velocity.state', 'analysis.acceleration.state', 'analysis.volume_quality.state', 'analysis.noise.state', 'analysis.correlation.state', 'analysis.relative_strength.state')
_EXPECTED_ID_ORDER = ('trend', 'momentum', 'volatility', 'volume', 'spread', 'candle', 'gap', 'session', 'time', 'velocity', 'acceleration', 'volume_quality', 'noise', 'correlation', 'relative_strength')
_EXPECTED_IDS = frozenset(_EXPECTED_ID_ORDER)
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CYCLES = "NO_CYCLES_YET"

_LIVE_ROW_FIELDS = (
    "analyzer_id", "id", "account_id", "broker", "symbol", "sequence",
    "weight", "confidence", "current_depth", "required_depth",
    "confidence_threshold", "threshold", "source_timestamp",
    "ready", "analysis_state", "score", "status",
    "direction", "strength",
)


def _slim_live_row(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in _LIVE_ROW_FIELDS if key in payload}

def _to_float(value: Any) -> float | None:
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if result == result else None

_TIMEFRAME_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}

def _timeframe_seconds(text_value: Any) -> float | None:
    text = str(text_value or "").strip().lower()
    if len(text) < 2 or text[-1] not in _TIMEFRAME_UNITS:
        return None
    try: amount = float(text[:-1])
    except ValueError: return None
    return amount * _TIMEFRAME_UNITS[text[-1]]

@section_atom("150", "150")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._expected = len(_EXPECTED_IDS)
        self._timeout_s = 5.0
        self._now = 0.0
        self._live_latest: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._live_batch: dict[tuple[str, str], dict[str, Any]] = {}
        self._live_batch_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._live_flush_timeout_s = 1.0
        self._live_forwarded = 0
        # X.md Build 4 connection 1: the slow (candle) cycle stores + batch.
        self._candle_latest: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
        self._candle_batch: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._candle_cycles = 0
        self._invalid = self._duplicates = self._late = self._echoes = 0
        self._health_seen = {"invalid": 0, "duplicates": 0, "late": 0}
        # Unit 150 closure phase 3: per-analyst self-declared registry.
        self._analyst_latest: dict[tuple[tuple[str, str, str], str], dict[str, Any]] = {}
        self._analyst_deliveries: dict[tuple[tuple[str, str, str], str], int] = {}
        self._panel_emitted = 0
        self._settings_applied = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeout_s = float(context.config["timeout_seconds"])
        self._live_flush_timeout_s = float(context.config.get("live_flush_timeout_s", 1.0))
        context.subscribe("market_data.candle_closed", self._on_candle_closed)
        context.subscribe(EVENT_TIME, self._on_time)
        context.subscribe(EVENT_SETTINGS_COMMAND, self._on_setting)
        for event, unit_id in zip(_UNIT_EVENTS, _EXPECTED_ID_ORDER):
            context.subscribe(event, self._unit_handler(unit_id))

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _live_lock(self, scope: tuple[str, str, str]) -> asyncio.Lock:
        lock = self._live_batch_locks.get(scope)
        if lock is None:
            lock = asyncio.Lock()
            self._live_batch_locks[scope] = lock
        return lock

    def _unit_handler(self, expected_id: str):
        async def handler(payload: dict[str, Any]) -> None:
            unit_id = str(payload.get("analyzer_id") or payload.get("id") or "")
            await self._on_live_state(payload, unit_id, expected_id)
        return handler

    async def _on_live_state(self, payload: dict[str, Any], unit_id: str,
                             expected_id: str | None) -> None:
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        symbol = str(payload.get("symbol") or payload.get("asset") or "").strip().upper()
        if (not symbol or unit_id not in _EXPECTED_IDS
                or (expected_id is not None and unit_id != expected_id)):
            self._invalid += 1
            return
        # X.md Build 4 connection 1 (owner seal 2026-08-23): candle results are
        # a SECOND collected cycle, not garbage -- a row without a live
        # sequence but with candle identity feeds the slow path.
        if "sequence" not in payload:
            await self._on_candle_state(payload, unit_id)
            return
        if not account or not broker:
            self._invalid += 1
            return
        try:
            sequence = int(payload.get("sequence"))
        except (TypeError, ValueError):
            self._invalid += 1
            return
        scope = (account, broker, symbol)
        results = self._live_latest.setdefault(scope, {})
        previous = results.get(unit_id)
        if previous is not None:
            previous_sequence = int(previous.get("sequence", -1))
            if sequence == previous_sequence:
                self._echoes += 1
                return
            if sequence < previous_sequence:
                self._duplicates += 1
                return
        results[unit_id] = _slim_live_row(payload)
        self._record_delivery(scope, unit_id, {
            "mode": MODE_LIVE, "timeframe": "tick", "period_start": sequence,
            "sequence": sequence,
            "direction": _to_float(payload.get("direction")),
            "strength": _to_float(payload.get("strength")),
            "confidence": _to_float(payload.get("confidence")),
            "weight": _to_float(payload.get("weight")),
            "ready": payload.get("ready") is True,
            "state": str(payload.get("analysis_state") or "")})
        source_ts = _to_float(payload.get("source_timestamp")) or 0.0
        # v2.9.0: the whole read-maybe_flush-write section is serialized per
        # scope. _flush_live below awaits (publish + panel emit) -- without
        # this lock, another unit's handler for the SAME scope can run in
        # that window, see no batch (already popped), open its own, and then
        # have it silently overwritten when this call resumes and writes its
        # own fresh batch unconditionally. Different scopes stay independent.
        async with self._live_lock(scope):
            batch = self._live_batch.get(scope)
            if batch is not None and source_ts > batch["source_ts"]:
                await self._flush_live(scope, "newer_tick")
                batch = self._live_batch.get(scope)
            if batch is None:
                batch = {"source_ts": source_ts, "arrived": set(), "opened_wall": self._now,
                         "account": account, "broker": broker, "symbol": symbol,
                         "trigger_unit": unit_id, "sequence": sequence,
                         "payload_source_ts": payload.get("source_timestamp"),
                         "payload_ts": payload.get("timestamp")}
                self._live_batch[scope] = batch
            batch["arrived"].add(unit_id)
            batch["trigger_unit"] = unit_id
            batch["sequence"] = sequence
            batch["payload_source_ts"] = payload.get("source_timestamp")
            batch["payload_ts"] = payload.get("timestamp")
            if len(batch["arrived"]) >= self._expected:
                await self._flush_live(scope, "all_units")

    def _record_delivery(self, scope: tuple[str, str, str], unit_id: str,
                         declared: dict[str, Any]) -> None:
        # Unit 150 closure phase 3: the analyst declares ITSELF -- what it
        # said, its own rhythm, and when. The aggregator invents nothing.
        declared["delivered_wall"] = self._now
        self._analyst_latest[(scope, unit_id)] = declared
        key = (scope, unit_id)
        self._analyst_deliveries[key] = self._analyst_deliveries.get(key, 0) + 1

    async def _on_candle_state(self, payload: dict[str, Any], unit_id: str) -> None:
        """Build 4 connection 1: the slow path -- store the latest candle row
        per (symbol, timeframe, cycle) and complete when the candle closes."""
        symbol = str(payload.get("symbol") or payload.get("asset") or "").strip().upper()
        timeframe = str(payload.get("timeframe") or "").strip()
        cycle_id = str(payload.get("cycle_id") or "").strip()
        if not symbol or not timeframe or not cycle_id:
            self._invalid += 1
            return
        key = (symbol, timeframe, cycle_id)
        self._candle_latest.setdefault(key, {})[unit_id] = dict(payload)
        candle_scope = (str(payload.get("account_id") or "").strip(),
                        str(payload.get("broker") or "").strip(), symbol)
        self._record_delivery(candle_scope, unit_id, {
            "mode": MODE_CANDLE, "timeframe": timeframe,
            "period_start": payload.get("period_start"), "sequence": None,
            "direction": _to_float(payload.get("direction")),
            "strength": _to_float(payload.get("strength")),
            "confidence": _to_float(payload.get("confidence")),
            "weight": _to_float(payload.get("weight")),
            "ready": payload.get("ready") is True,
            "state": str(payload.get("analysis_state") or "")})
        # Phase 3 rule: a delivery IS an update -- the candle rhythm does not
        # wait for its slow flush; the panel moves the moment an analyst speaks.
        if candle_scope[0] and candle_scope[1]:
            await self._emit_panel(candle_scope)
        batch = self._candle_batch.get(key)
        if batch is not None:
            batch["arrived"].add(unit_id)
            if len(batch["arrived"]) >= self._expected:
                await self._flush_candle(key, "all_units")

    async def _on_candle_closed(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        symbol = str(payload.get("symbol") or payload.get("asset") or "").strip().upper()
        timeframe = str(payload.get("timeframe") or "").strip()
        period_start = payload.get("period_start", payload.get("timestamp"))
        if not account or not broker or not symbol or not timeframe:
            self._invalid += 1
            return
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe,
                                period_start=period_start)
        key = (symbol, timeframe, cycle_id)
        store = self._candle_latest.setdefault(key, {})
        self._candle_batch[key] = {"account": account, "broker": broker,
                                   "symbol": symbol, "timeframe": timeframe,
                                   "cycle_id": cycle_id, "period_start": period_start,
                                   "arrived": set(store.keys()), "opened_wall": self._now}
        if len(self._candle_batch[key]["arrived"]) >= self._expected:
            await self._flush_candle(key, "all_units")

    async def _flush_candle(self, key: tuple[str, str, str], reason: str) -> None:
        batch = self._candle_batch.pop(key, None)
        if batch is None or self._context is None:
            return
        results = self._candle_latest.get(key, {})
        if not results:
            return
        missing = [item for item in _EXPECTED_ID_ORDER if item not in results]
        await self._context.publish(EVENT_OUT, {
            "account_id": batch["account"], "broker": batch["broker"],
            "symbol": batch["symbol"], "asset": batch["symbol"],
            "cycle_id": batch["cycle_id"], "timeframe": batch["timeframe"],
            "analysis_mode": MODE_CANDLE,
            "period_start": batch["period_start"],
            "results": {unit: dict(row) for unit, row in results.items()},
            "results_contract": "candle_v1",
            "expected": self._expected, "present": len(results),
            "accepted": len(results), "missing": missing,
            "complete": not missing,
            "cycle_status": "complete" if not missing else "incomplete",
            "close_reason": "candle_batch_%s" % reason,
        })
        self._candle_cycles += 1
        await self._emit_panel((batch["account"], batch["broker"], batch["symbol"]))

    async def _flush_live(self, scope: tuple[str, str], reason: str) -> None:
        batch = self._live_batch.pop(scope, None)
        if batch is None or self._context is None:
            return
        results = self._live_latest.get(scope, {})
        if not results:
            return
        account = batch["account"]; broker = batch["broker"]; symbol = batch["symbol"]
        sequence = batch["sequence"]; unit_id = batch["trigger_unit"]
        present = len(results)
        missing = [item for item in _EXPECTED_ID_ORDER if item not in results]
        active_weight = sum(float(item.get("weight", 0.0)) for item in results.values()
                            if item.get("analysis_state") == STATE_READY and item.get("ready") is True)
        available_weight = sum(float(item.get("weight", 0.0))
                               for item in results.values())
        ratios = ({key: round(float(value.get("weight", 0.0)) /
                              available_weight * 100.0, 4)
                   for key, value in results.items()}
                  if available_weight > 0 else {})
        trigger_cycle = cycle_key_of(
            {"account_id": account, "broker": broker}, symbol=symbol,
            timeframe="tick", period_start=sequence)
        await self._context.publish(EVENT_OUT, {
            "account_id": account, "broker": broker,
            "symbol": symbol, "asset": symbol,
            "cycle_id": trigger_cycle, "timeframe": "tick",
            "analysis_mode": MODE_LIVE, "live_contract_version": 1,
            "source_timestamp": batch["payload_source_ts"],
            "timestamp": batch["payload_ts"],
            "trigger_analyzer_id": unit_id, "sequence": sequence,
            "results": {key: dict(value) for key, value in results.items()},
            "results_contract": "slim_v1",
            "expected": self._expected, "present": present, "accepted": present,
            "missing": missing, "complete": not missing, "cycle_status": "live_latest",
            "close_reason": "tick_batch_%s" % reason, "active_weight": round(active_weight, 4),
            "available_weight": round(available_weight, 4),
            "missing_weight": round(max(0.0, available_weight - active_weight), 4),
            "unreported_analyzers": missing, "ratios": ratios,
        })
        self._live_forwarded += 1
        await self._emit_panel((account, broker, symbol))

    async def _emit_panel(self, scope: tuple[str, str, str]) -> None:
        # Unit 150 closure phase 3: the analysts panel -- one event, one row
        # per expected analyst, everything self-declared by the analyst: what
        # it said, its rhythm, its delivery count, its age, its next expected
        # time (candle rhythm only; a tick rhythm is continuous -> None).
        if self._context is None:
            return
        account, broker, symbol = scope
        rows = []
        for unit_id in _EXPECTED_ID_ORDER:
            declared = self._analyst_latest.get((scope, unit_id))
            if declared is None:
                rows.append({"id": unit_id, "present": False})
                continue
            next_expected = None
            if declared.get("mode") == MODE_CANDLE:
                seconds = _timeframe_seconds(declared.get("timeframe"))
                start = _to_float(declared.get("period_start"))
                if seconds is not None and start is not None:
                    next_expected = start + seconds
            rows.append({"id": unit_id, "present": True,
                         "mode": declared.get("mode"),
                         "timeframe": declared.get("timeframe"),
                         "period_start": declared.get("period_start"),
                         "sequence": declared.get("sequence"),
                         "direction": declared.get("direction"),
                         "strength": declared.get("strength"),
                         "confidence": declared.get("confidence"),
                         "weight": declared.get("weight"),
                         "ready": declared.get("ready"),
                         "state": declared.get("state"),
                         "deliveries": self._analyst_deliveries.get((scope, unit_id), 0),
                         "delivered_at": (declared.get("delivered_wall")
                                          if declared.get("delivered_wall") is not None else None),
                         "age_s": (round(self._now - declared["delivered_wall"], 1)
                                   if declared.get("delivered_wall") is not None else None),
                         "next_expected_at": next_expected})
        present = sum(1 for row in rows if row.get("present"))
        await self._context.publish(EVENT_PANEL, {
            "account_id": account or None, "broker": broker or None,
            "symbol": symbol, "analysts": rows,
            "expected": self._expected, "present": present,
            "missing": [row["id"] for row in rows if not row.get("present")]})
        self._panel_emitted += 1

    async def _on_setting(self, payload: dict[str, Any]) -> None:
        """Applies the ANALYSIS_SPEED dial from the dashboard (analysis
        speed contract v1.0). The value is written to the dial registry
        and reaches every live analyzer via the base snapshot -- no
        restart, no risk-path touch (strict separation Sec.6/Sec.33)."""
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        applied = apply_command(payload, atom_id="150")
        if applied is None:
            return
        self._settings_applied += 1
        await self._context.publish(EVENT_SETTINGS_STATE, {"atom": "150", **applied})

    async def _on_time(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        now = _to_float(payload.get("official_time"))
        if now is None: return
        self._now = now
        aged = [scope for scope, batch in self._live_batch.items()
                if batch["opened_wall"] > 0 and now - batch["opened_wall"] > self._live_flush_timeout_s]
        for scope in aged: await self._flush_live(scope, "timeout")
        # v2.7.0 (2026-08-25): the candle batch gets the same escape the live
        # batch always had. Measured: one silent analyst froze the slow cycle
        # FOREVER (7 candles closed, 14/15 delivered, zero slow cycles) --
        # nobody waits for anybody: after the declared timeout the batch is
        # forwarded with what arrived and the absentees named in `missing`.
        aged_candles = [key for key, batch in self._candle_batch.items()
                        if batch.get("opened_wall", 0) > 0
                        and now - batch["opened_wall"] > self._timeout_s]
        for key in aged_candles: await self._flush_candle(key, "timeout")

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"live_forwarded": self._live_forwarded, "live_scopes": len(self._live_latest),
            "expected": self._expected, "invalid": self._invalid, "duplicates": self._duplicates,
            "late": self._late, "echoes": self._echoes,
            "analysts_tracked": len(self._analyst_latest), "panel_emitted": self._panel_emitted,
            "analysis_speed": speed_value(), "settings_applied": self._settings_applied}
        if self._live_forwarded == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CYCLES, details=details)
        new_invalid = self._invalid - self._health_seen["invalid"]
        new_duplicates = self._duplicates - self._health_seen["duplicates"]
        new_late = self._late - self._health_seen["late"]
        self._health_seen = {"invalid": self._invalid,
                             "duplicates": self._duplicates, "late": self._late}
        state = HealthState.DEGRADED if new_invalid or new_duplicates or new_late else HealthState.HEALTHY
        return HealthStatus(state=state,
            message="live_forwarded=%d new_invalid=%d new_duplicate=%d new_late=%d echoes=%d" %
            (self._live_forwarded, new_invalid, new_duplicates, new_late, self._echoes), details=details)
