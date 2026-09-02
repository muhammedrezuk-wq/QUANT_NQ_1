from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.time.state"

METHOD = "calendar_position"

PHASE_WEEK_OPEN = "week_open"
PHASE_WEEK_CLOSE = "week_close"
PHASE_MID_WEEK = "mid_week"
PHASE_WEEKEND = "weekend"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_SECONDS_PER_HOUR = 3600.0
_SECONDS_PER_DAY = 86400.0
_MONTHS_PER_QUARTER = 3
_SATURDAY = 5
_SCORE_EDGE = 100
_SCORE_WEEKEND = 60
_SCORE_MID = 40

_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday",
                  "friday", "saturday", "sunday")


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("150", "159")
@live_analyzer("time", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._utc_offset = 0
        self._week_open_day = 0
        self._week_close_day = 4
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._utc_offset = int(cfg["utc_offset_hours"])
        self._week_open_day = int(cfg["week_open_day"])
        self._week_close_day = int(cfg["week_close_day"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: audited for the candle-memory-loss defect that
    # hit the other analyzers in this section (151-165) -- this one has none
    # to lose. Every emission is a pure function of the incoming candle's own
    # timestamp; nothing here accumulates across candles. The only
    # insufficient_data path is a missing timestamp on the CURRENT candle,
    # not a warm-up window, so `_candles_seen`/`_emitted` are diagnostic
    # counters only and do not gate readiness. No snapshot()/restore() added:
    # inventing a save for state that does not exist would just add a place
    # for the next defect to hide.

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        self._candles_seen += 1
        ts = _to_float(payload.get("period_start", payload.get("timestamp")))
        if ts is None:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        await self._emit(symbol, timeframe, cycle_id, ts)

    def _phase(self, weekday: int) -> str:
        if weekday >= _SATURDAY:
            return PHASE_WEEKEND
        if weekday == self._week_open_day:
            return PHASE_WEEK_OPEN
        if weekday == self._week_close_day:
            return PHASE_WEEK_CLOSE
        return PHASE_MID_WEEK

    @staticmethod
    def _score(phase: str) -> int:
        if phase in (PHASE_WEEK_OPEN, PHASE_WEEK_CLOSE):
            return _SCORE_EDGE
        if phase == PHASE_WEEKEND:
            return _SCORE_WEEKEND
        return _SCORE_MID

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    ts: float) -> None:
        if self._context is None:
            return
        shifted = ts + self._utc_offset * _SECONDS_PER_HOUR
        moment = datetime.fromtimestamp(shifted, timezone.utc)
        next_day = datetime.fromtimestamp(shifted + _SECONDS_PER_DAY, timezone.utc)
        weekday = moment.weekday()
        month = moment.month
        quarter = (month - 1) // _MONTHS_PER_QUARTER + 1
        is_month_start = moment.day == 1
        is_month_end = next_day.month != month
        is_quarter_end = is_month_end and (month % _MONTHS_PER_QUARTER == 0)
        phase = self._phase(weekday)
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "time", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": phase, "score": self._score(phase),
            "confidence": 1.0, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe,
                         "hour": moment.hour, "weekday": weekday,
                         "weekday_name": _WEEKDAY_NAMES[weekday], "month": month,
                         "quarter": quarter, "day_of_month": moment.day,
                         "is_month_start": is_month_start,
                         "is_month_end": is_month_end,
                         "is_quarter_end": is_quarter_end}})
        self._emitted += 1

    async def _emit_insufficient(self, symbol: str, timeframe: str,
                                 cycle_id: str) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "time", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_INSUFFICIENT, "signal": PHASE_MID_WEEK, "score": 0,
            "confidence": 0.0, "quality": QUALITY_GOOD, "warnings": ["no_timestamp"],
            "metadata": {"method": METHOD, "timeframe": timeframe}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d" % (self._candles_seen, self._emitted),
            details={"candles": self._candles_seen, "emitted": self._emitted})
