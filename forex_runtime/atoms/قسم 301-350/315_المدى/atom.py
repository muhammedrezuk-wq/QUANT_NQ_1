from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of
from shared.atom_evidence import window_evidence

WEIGHT = 5.882353
ATOM_VERSION = "1.2.0"

EVENT_IN = "market.tick.validated"
EVENT_OUT = "stats.range.state"

METHOD = "rolling_min_max_range"
ID_RANGE = "range"

SIGNAL_AT_HIGH = "at_high"
SIGNAL_AT_LOW = "at_low"
SIGNAL_MID = "mid"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_data_points"
WARN_ZERO_RANGE = "zero_range"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TICKS = "NO_TICKS_YET"

_MIN_POINTS = 2
_VALUE_DP = 6
_PERCENT = 100.0
_MID = 0.5


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("300", "315")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_size = 20
        self._high_band = 0.8
        self._low_band = 0.2
        self._state: dict[tuple, deque] = {}
        self._ticks_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._window_size = int(context.config["window_size"])
        self._high_band = float(context.config["high_band"])
        self._low_band = float(context.config["low_band"])
        context.subscribe(EVENT_IN, self._on_tick)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        price = _to_float(payload.get("price"))
        if not symbol or price is None:
            return
        symbol = str(symbol)
        timeframe = "tick"
        period_start = str(payload.get("sequence") or "")
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        window = self._state.get(key)
        if window is None:
            window = deque(maxlen=self._window_size)
            self._state[key] = window
        window.append(price)
        self._ticks_seen += 1
        await self._emit(symbol, timeframe, cycle_id, window)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    window: deque) -> None:
        if self._context is None:
            return
        count = len(window)
        base = {"symbol": symbol, "id": ID_RANGE, "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe, "window": self._window_size,
                "count": count}
        if count < _MIN_POINTS:
            await self._context.publish(EVENT_OUT, {
                **window_evidence(have=len(window), need=self._window_size),
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_MID,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        lo = min(window)
        hi = max(window)
        last = window[-1]
        rng = hi - lo
        warnings: list = []
        if rng <= 0.0:
            position = _MID
            signal = SIGNAL_MID
            warnings = [WARN_ZERO_RANGE]
        else:
            position = (last - lo) / rng
            if position >= self._high_band:
                signal = SIGNAL_AT_HIGH
            elif position <= self._low_band:
                signal = SIGNAL_AT_LOW
            else:
                signal = SIGNAL_MID
        confidence = round(count / self._window_size, 2) if self._window_size > 0 else 0.0
        meta.update({"min": round(lo, _VALUE_DP), "max": round(hi, _VALUE_DP),
                     "range": round(rng, _VALUE_DP), "position": round(position, _VALUE_DP)})
        await self._context.publish(EVENT_OUT, {
            **window_evidence(have=len(window), need=self._window_size),
            **base, "status": STATUS_OK, "signal": signal,
            "score": int(round(position * _PERCENT)),
            "confidence": min(100.0, confidence * 100.0), "quality": QUALITY_GOOD, "weight": WEIGHT,
            "analysis_state": "READY", "ready": True,
            "warnings": warnings, "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._ticks_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_TICKS,
                                details={"tracked": len(self._state)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="ticks=%d emitted=%d tracked=%d" % (
                self._ticks_seen, self._emitted, len(self._state)),
            details={"ticks": self._ticks_seen, "emitted": self._emitted,
                     "tracked": len(self._state)})
