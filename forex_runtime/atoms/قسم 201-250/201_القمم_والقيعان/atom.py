from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.analysis_speed import speed_factor, speed_value
from shared.atom_evidence import window_evidence
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "1.3.0"


_MAX_SPEED_MULTIPLIER = 5


def _speed_window(base: int, symbol: str, account: str, floor: int) -> int:
    """Window derived from the speed key -- the structure section shares
    this one key (2026-08-26: section 200 wired to the same control point).
    Base is the manifest's lookback, default 50."""
    factor = speed_factor(speed_value(account, symbol))
    return max(floor, min(base * _MAX_SPEED_MULTIPLIER, int(round(base * factor))))

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "structure.swing.state"

METHOD = "fractal_center"
ID_SWING = "swing"

SIGNAL_HIGH = "swing_high"
SIGNAL_LOW = "swing_low"
SIGNAL_NONE = "none"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"
REASON_ALL_REJECTED = "ALL_CANDLES_REJECTED_SO_FAR"

_SCORE_MAX = 100.0
_PRICE_DP = 4
_PROM_DP = 4
# Bound only (external feed boundary -- a malformed/garbage symbol name is
# untrusted input, unlike the internal 201->251 contract): once this many
# distinct rejected symbols are tracked, a new symbol's rejections still
# count toward the total but stop getting their own breakdown entry.
_MAX_TRACKED_REJECTED_SYMBOLS = 64


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("200", "201")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._lookback = 2
        self._window_size = 5
        self._state: dict[tuple, dict[str, Any]] = {}
        self._candles_seen = 0
        self._emitted = 0
        self._swings_found = 0
        self._rejected_candles = 0
        self._rejected_by_symbol: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._lookback = int(context.config["lookback"])
        self._window_size = self._lookback * 2 + 1
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
        if not symbol or high is None or low is None or close is None:
            self._rejected_candles += 1
            reject_key = str(symbol) if symbol else "UNKNOWN"
            if reject_key in self._rejected_by_symbol or \
                    len(self._rejected_by_symbol) < _MAX_TRACKED_REJECTED_SYMBOLS:
                self._rejected_by_symbol[reject_key] = self._rejected_by_symbol.get(reject_key, 0) + 1
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        st = self._state.get(key)
        if st is None:
            st = {"window": deque(maxlen=self._lookback * 5 * 2 + 1)}
            self._state[key] = st
        st["window"].append({"high": high, "low": low, "close": close,
                             "period_start": period_start})
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, close, st,
                         str(payload.get("account_id") or ""))

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    close: float, st: dict[str, Any], account: str = "") -> None:
        if self._context is None:
            return
        # The speed key drives the swing-high/low window -- the source of
        # the whole structure section (202-208 derive from these swings):
        # at 50 this is the manifest value, literally.
        w_look = _speed_window(self._lookback, symbol, account, 1)
        need = w_look * 2 + 1
        window = list(st["window"])[-need:]
        base = {"symbol": symbol, "id": ID_SWING, "cycle_id": cycle_id,
                "timeframe": timeframe,
                **window_evidence(have=len(window), need=need)}
        meta = {"method": METHOD, "timeframe": timeframe,
                "lookback": w_look, "close": close}
        if len(window) < need:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_NONE,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        center = window[w_look]
        others = [window[i] for i in range(len(window)) if i != w_look]
        highest_other = max(c["high"] for c in others)
        lowest_other = min(c["low"] for c in others)
        signal = SIGNAL_NONE
        price = 0.0
        gap = 0.0
        if center["high"] > highest_other:
            signal = SIGNAL_HIGH
            price = center["high"]
            gap = center["high"] - highest_other
        elif center["low"] < lowest_other:
            signal = SIGNAL_LOW
            price = center["low"]
            gap = lowest_other - center["low"]
        if signal == SIGNAL_NONE:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_OK, "signal": SIGNAL_NONE, "score": 0,
                "confidence": 0.0, "quality": QUALITY_GOOD, "warnings": [],
                "metadata": meta})
            self._emitted += 1
            return
        win_high = max(c["high"] for c in window)
        win_low = min(c["low"] for c in window)
        span = win_high - win_low
        prominence = gap / span if span > 0 else 0.0
        score = int(round(min(1.0, prominence) * _SCORE_MAX))
        meta.update({"price": round(price, _PRICE_DP),
                     "swing_time": center["period_start"],
                     "prominence": round(prominence, _PROM_DP)})
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": 1.0, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1
        self._swings_found += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            if self._rejected_candles > 0:
                return HealthStatus(
                    state=HealthState.DEGRADED, message=REASON_ALL_REJECTED,
                    details={"tracked": len(self._state), "rejected": self._rejected_candles,
                             "rejected_by_symbol": dict(self._rejected_by_symbol)})
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES,
                                details={"tracked": len(self._state), "rejected": 0})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d swings=%d tracked=%d rejected=%d" % (
                self._candles_seen, self._emitted, self._swings_found, len(self._state),
                self._rejected_candles),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "swings": self._swings_found, "tracked": len(self._state),
                     "rejected": self._rejected_candles,
                     "rejected_by_symbol": dict(self._rejected_by_symbol)})
