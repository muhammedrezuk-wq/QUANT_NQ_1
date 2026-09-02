from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "1.2.0"

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "liquidity.fvg.state"

METHOD = "three_candle_gap"
ID_FVG = "fvg"

SIGNAL_BULL = "fvg_bullish"
SIGNAL_BEAR = "fvg_bearish"
SIGNAL_NONE = "none"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_FVG_WINDOW = 3
_PRICE_DP = 4
_CONF_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("250", "255")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._state: dict[tuple, deque] = {}
        self._candles_seen = 0
        self._fvgs = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        high = _to_float(payload.get("high"))
        low = _to_float(payload.get("low"))
        close = _to_float(payload.get("close"))
        if not symbol or high is None or low is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        window = self._state.get(key)
        if window is None:
            window = deque(maxlen=_FVG_WINDOW)
            self._state[key] = window
        window.append({"high": high, "low": low})
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, close, window)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, close: Any,
                    window: deque) -> None:
        if self._context is None:
            return
        base = {"symbol": symbol, "id": ID_FVG, "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe, "close": close}
        if len(window) < _FVG_WINDOW:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_NONE,
                "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        first = window[0]
        third = window[-1]
        signal = SIGNAL_NONE
        gap_top = None
        gap_bottom = None
        if first["high"] < third["low"]:
            signal = SIGNAL_BULL
            gap_bottom = first["high"]
            gap_top = third["low"]
        elif first["low"] > third["high"]:
            signal = SIGNAL_BEAR
            gap_top = first["low"]
            gap_bottom = third["high"]
        if signal != SIGNAL_NONE:
            self._fvgs += 1
        meta.update({"gap_top": round(gap_top, _PRICE_DP) if gap_top is not None else None,
                     "gap_bottom": round(gap_bottom, _PRICE_DP) if gap_bottom is not None else None})
        # Confidence = gap_size / window_range (not binary); window_range
        # always >= gap_size since it spans all 3 candles in the window.
        window_high = max(c["high"] for c in window)
        window_low = min(c["low"] for c in window)
        window_range = window_high - window_low
        gap_size = (gap_top - gap_bottom) if (gap_top is not None and gap_bottom is not None) else None
        confidence = 0.0
        if signal != SIGNAL_NONE and gap_size is not None and window_range > 0:
            confidence = round(min(1.0, gap_size / window_range), _CONF_DP)
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": signal,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES,
                                details={"tracked": len(self._state)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d fvgs=%d tracked=%d" % (
                self._candles_seen, self._fvgs, len(self._state)),
            details={"candles": self._candles_seen, "fvgs": self._fvgs,
                     "tracked": len(self._state)})
