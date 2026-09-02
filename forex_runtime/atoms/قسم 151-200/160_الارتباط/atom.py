from __future__ import annotations

import math
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.correlation.state"

METHOD = "rolling_pearson"

SIG_POSITIVE = "positive"
SIG_NEGATIVE = "negative"
SIG_WEAK = "weak"
SIG_ANCHOR = "anchor"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_overlap"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_PERCENT = 100.0
_MIN_POINTS = 10
_CONF_SIGNAL = 0.7
_CONF_WEAK = 0.4
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


@section_atom("150", "160")
@live_analyzer("correlation", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._anchor = "USTEC"
        self._window = 30
        self._corr_threshold = 0.5
        self._state: dict[tuple, dict[str, Any]] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._anchor = str(cfg["anchor_symbol"])
        self._window = int(cfg["window"])
        self._corr_threshold = float(cfg["corr_threshold"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping this atom's per-symbol return history on
    # every restart. Measured effect: the anchor symbol's own return window
    # (`window`=30 by default) had to rebuild from zero, and every peer
    # needs 10 overlapping points with it before it leaves
    # insufficient_data -- so a reboot silenced correlation for up to 30
    # candles every time. The wrapper now chains both states --
    # "live_analysis" for its own tick memory, "atom" for this return
    # history -- so this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "scopes": [{"symbol": symbol, "timeframe": timeframe,
                        "prev_close": st["prev_close"],
                        "rets": [[period, value] for period, value in st["rets"]]}
                       for (symbol, timeframe), st in self._state.items()],
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._candles_seen = int(state.get("candles_seen") or 0)
        self._emitted = int(state.get("emitted") or 0)
        self._state = {}
        for row in state.get("scopes") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            rets: deque = deque(maxlen=self._window)
            for item in row.get("rets") or []:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                value = _to_float(item[1])
                if value is None:
                    continue
                rets.append((item[0], value))
            self._state[(symbol, str(row.get("timeframe") or ""))] = {
                "prev_close": _to_float(row.get("prev_close")), "rets": rets,
            }

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        close = _to_float(payload.get("close"))
        if not symbol or close is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        self._candles_seen += 1
        st = self._state.get(key)
        if st is None:
            st = {"prev_close": close, "rets": deque(maxlen=self._window)}
            self._state[key] = st
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        prev_close = st["prev_close"]
        st["prev_close"] = close
        if not prev_close:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        ret = (close - prev_close) / prev_close
        st["rets"].append((period_start, ret))
        if symbol == self._anchor:
            await self._emit(symbol, timeframe, cycle_id, SIG_ANCHOR,
                             int(_PERCENT), 1.0, 1.0, len(st["rets"]))
            return
        anchor_st = self._state.get((self._anchor, timeframe))
        if anchor_st is None:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        anchor_map = dict(anchor_st["rets"])
        xs = []
        ys = []
        for period, value in st["rets"]:
            other = anchor_map.get(period)
            if other is not None:
                xs.append(value)
                ys.append(other)
        if len(xs) < _MIN_POINTS:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        corr = _pearson(xs, ys)
        if corr > self._corr_threshold:
            signal = SIG_POSITIVE
        elif corr < -self._corr_threshold:
            signal = SIG_NEGATIVE
        else:
            signal = SIG_WEAK
        score = int(round(min(_PERCENT, abs(corr) * _PERCENT)))
        confidence = _CONF_WEAK if signal == SIG_WEAK else _CONF_SIGNAL
        await self._emit(symbol, timeframe, cycle_id, signal, score, confidence,
                         corr, len(xs))

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, signal: str,
                    score: int, confidence: float, corr: float, points: int) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "correlation", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe, "anchor": self._anchor,
                         "correlation": round(corr, _DP), "points": points}})
        self._emitted += 1

    async def _emit_insufficient(self, symbol: str, timeframe: str,
                                 cycle_id: str) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "correlation", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_INSUFFICIENT, "signal": SIG_WEAK, "score": 0,
            "confidence": 0.0, "quality": QUALITY_LOW, "warnings": [WARN_INSUFFICIENT],
            "metadata": {"method": METHOD, "timeframe": timeframe, "anchor": self._anchor}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d tracked=%d" % (
                self._candles_seen, self._emitted, len(self._state)),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "tracked": len(self._state)})
