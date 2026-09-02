from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.session.state"

METHOD = "clock_sessions"

SESSION_ASIA = "asia"
SESSION_LONDON = "london"
SESSION_NY = "new_york"
SESSION_OVERLAP = "overlap"
SESSION_CLOSED = "closed"
SESSION_CRYPTO = "crypto_24h"
SESSION_OPEN = "open"

STATUS_OK = "ok"

QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_SECONDS_PER_HOUR = 3600.0
_HOURS_PER_DAY = 24
_SCORE_PER_SESSION = 50.0
_SCORE_MAX = 100.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _in_window(hour: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


@section_atom("150", "158")
@live_analyzer("session", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._utc_offset = 0
        self._asia = (0, 9)
        self._london = (7, 16)
        self._ny = (12, 21)
        self._crypto: set[str] = set()
        self._always_open: set[str] = set()
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._utc_offset = int(cfg["utc_offset_hours"])
        self._asia = (int(cfg["asia_start"]), int(cfg["asia_end"]))
        self._london = (int(cfg["london_start"]), int(cfg["london_end"]))
        self._ny = (int(cfg["ny_start"]), int(cfg["ny_end"]))
        self._crypto = {str(s).upper() for s in cfg["crypto_symbols"]}
        self._always_open = {str(s).upper() for s in cfg["always_open_symbols"]}
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
    # timestamp and the symbol lists from config; nothing here accumulates
    # across candles, so `_candles_seen`/`_emitted` are diagnostic counters
    # only and do not gate readiness (there is no insufficient_data state at
    # all). No snapshot()/restore() added: inventing a save for state that
    # does not exist would just add a place for the next defect to hide.

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        ts = _to_float(payload.get("period_start", payload.get("timestamp")))
        if not symbol or ts is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, ts)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, ts: float) -> None:
        if self._context is None:
            return
        hour = (int(ts // _SECONDS_PER_HOUR) + self._utc_offset) % _HOURS_PER_DAY
        active = []
        if _in_window(hour, self._asia[0], self._asia[1]):
            active.append(SESSION_ASIA)
        if _in_window(hour, self._london[0], self._london[1]):
            active.append(SESSION_LONDON)
        if _in_window(hour, self._ny[0], self._ny[1]):
            active.append(SESSION_NY)
        units = len(active)
        if not active:
            su = symbol.upper()
            if su in self._crypto:
                signal = SESSION_CRYPTO
                units = 1
            elif su in self._always_open:
                signal = SESSION_OPEN
                units = 1
            else:
                signal = SESSION_CLOSED
        elif len(active) >= 2:
            signal = SESSION_OVERLAP
        else:
            signal = active[0]
        score = int(round(min(_SCORE_MAX, units * _SCORE_PER_SESSION)))
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "session", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": score, "confidence": 1.0,
            "quality": QUALITY_GOOD, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe, "hour_utc": hour,
                         "active": active}})
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
