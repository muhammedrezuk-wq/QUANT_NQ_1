from __future__ import annotations

import math
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.atom_evidence import window_evidence
from shared.cycle_identity import cycle_key_of

WEIGHT = 5.882353
ATOM_VERSION = "1.2.0"

EVENT_IN = "market.tick.validated"
EVENT_OUT = "stats.correlation.state"

METHOD = "rolling_pearson_returns"
ID_CORRELATION = "correlation"

SIGNAL_STRONG = "strong"
SIGNAL_MODERATE = "moderate"
SIGNAL_WEAK = "weak"
SIGNAL_REFERENCE = "reference"

DIRECTION_POSITIVE = "positive"
DIRECTION_INVERSE = "inverse"
DIRECTION_NONE = "none"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_overlap"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TICKS = "NO_TICKS_YET"

_PERCENT = 100.0
_MIN_OVERLAP = 10
_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _pearson(xs: list, ys: list) -> float:
    n = len(xs)
    if n == 0:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = 0.0
    var_x = 0.0
    var_y = 0.0
    for x, y in zip(xs, ys):
        dx = x - mean_x
        dy = y - mean_y
        cov += dx * dy
        var_x += dx * dx
        var_y += dy * dy
    denom = math.sqrt(var_x * var_y)
    return cov / denom if denom > 0.0 else 0.0


@section_atom("300", "307")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._reference = "USTEC"
        self._window = 30
        self._strong = 0.7
        self._moderate = 0.4
        self._state: dict[tuple, dict[str, Any]] = {}
        self._ticks_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._reference = str(cfg["reference_symbol"])
        self._window = int(cfg["window"])
        self._strong = float(cfg["strong_threshold"])
        self._moderate = float(cfg["moderate_threshold"])
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
        self._ticks_seen += 1
        st = self._state.get(key)
        if st is None:
            st = {"prev_close": price, "rets": deque(maxlen=self._window)}
            self._state[key] = st
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        prev_close = st["prev_close"]
        st["prev_close"] = price
        if not prev_close:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        ret = (price - prev_close) / prev_close
        st["rets"].append((period_start, ret))
        if symbol == self._reference:
            await self._emit(symbol, timeframe, cycle_id, SIGNAL_REFERENCE,
                             DIRECTION_NONE, int(_PERCENT), 1.0, 1.0, len(st["rets"]))
            return
        ref_st = self._state.get((self._reference, timeframe))
        if ref_st is None:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        ref_map = dict(ref_st["rets"])
        xs = []
        ys = []
        for period, value in st["rets"]:
            other = ref_map.get(period)
            if other is not None:
                xs.append(value)
                ys.append(other)
        points = len(xs)
        if points < _MIN_OVERLAP:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        corr = _pearson(xs, ys)
        mag = abs(corr)
        if mag >= self._strong:
            signal = SIGNAL_STRONG
        elif mag >= self._moderate:
            signal = SIGNAL_MODERATE
        else:
            signal = SIGNAL_WEAK
        if corr > 0.0:
            direction = DIRECTION_POSITIVE
        elif corr < 0.0:
            direction = DIRECTION_INVERSE
        else:
            direction = DIRECTION_NONE
        score = int(round(min(_PERCENT, mag * _PERCENT)))
        confidence = round(min(1.0, points / self._window), 2) if self._window > 0 else 0.0
        await self._emit(symbol, timeframe, cycle_id, signal, direction, score,
                         confidence, corr, points)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, signal: str,
                    direction: str, score: int, confidence: float, corr: float,
                    points: int) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_CORRELATION, "cycle_id": cycle_id,
            "timeframe": timeframe,
            **window_evidence(have=points, need=self._window),
            "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "weight": WEIGHT,
            "analysis_state": "READY", "ready": True, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe,
                         "reference": self._reference, "direction": direction,
                         "value": round(corr, _DP), "points": points}})
        self._emitted += 1

    async def _emit_insufficient(self, symbol: str, timeframe: str,
                                 cycle_id: str) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_CORRELATION, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_INSUFFICIENT, "signal": SIGNAL_WEAK, "score": 0,
            "confidence": 0.0, "quality": QUALITY_LOW, "warnings": [WARN_INSUFFICIENT],
            "metadata": {"method": METHOD, "timeframe": timeframe,
                         "reference": self._reference, "direction": DIRECTION_NONE}})
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
