from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.candle.state"

METHOD = "candlestick"

PAT_NONE = "none"
PAT_DOJI = "doji"
PAT_MARUBOZU = "marubozu"
PAT_PIN = "pin_bar"
PAT_ENGULFING = "engulfing"
PAT_INSIDE = "inside"
PAT_OUTSIDE = "outside"

DIR_BULL = "bullish"
DIR_BEAR = "bearish"
DIR_NONE = "none"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_PERCENT = 100.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("150", "156")
@live_analyzer("candle", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._doji_body = 0.1
        self._marubozu_body = 0.85
        self._pin_wick = 0.6
        self._pin_body = 0.3
        self._prev: dict[tuple, dict[str, float]] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._doji_body = float(cfg["doji_body_ratio"])
        self._marubozu_body = float(cfg["marubozu_body_ratio"])
        self._pin_wick = float(cfg["pin_wick_ratio"])
        self._pin_body = float(cfg["pin_body_ratio"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping the single previous candle this atom compares
    # against on every restart. Measured effect: pattern detection needs one
    # prior candle -- without it, every restart re-published
    # insufficient_data for the first candle after boot regardless of how
    # much history had already been seen. The wrapper now chains both
    # states -- "live_analysis" for its own tick memory, "atom" for this
    # previous-candle memory -- so this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "scopes": [{"symbol": symbol, "timeframe": timeframe, **prev}
                       for (symbol, timeframe), prev in self._prev.items()],
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._candles_seen = int(state.get("candles_seen") or 0)
        self._emitted = int(state.get("emitted") or 0)
        self._prev = {}
        for row in state.get("scopes") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            o = _to_float(row.get("open"))
            h = _to_float(row.get("high"))
            low = _to_float(row.get("low"))
            c = _to_float(row.get("close"))
            if o is None or h is None or low is None or c is None:
                continue
            self._prev[(symbol, str(row.get("timeframe") or ""))] = {
                "open": o, "high": h, "low": low, "close": c,
            }

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        o = _to_float(payload.get("open"))
        h = _to_float(payload.get("high"))
        low = _to_float(payload.get("low"))
        c = _to_float(payload.get("close"))
        if not symbol or o is None or h is None or low is None or c is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        prev = self._prev.get(key)
        self._prev[key] = {"open": o, "high": h, "low": low, "close": c}
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, o, h, low, c, prev)

    def _detect(self, o: float, h: float, low: float, c: float,
                prev: dict[str, float] | None) -> tuple:
        rng = h - low
        if rng <= 0:
            return PAT_NONE, DIR_NONE, 0.0
        body = abs(c - o)
        body_ratio = body / rng
        upper = h - max(o, c)
        lower = min(o, c) - low
        bull = c > o
        direction = DIR_BULL if bull else DIR_BEAR
        if prev is not None:
            cur_top, cur_bot = max(o, c), min(o, c)
            p_top, p_bot = max(prev["open"], prev["close"]), min(prev["open"], prev["close"])
            p_bull = prev["close"] > prev["open"]
            if cur_top >= p_top and cur_bot <= p_bot and bull != p_bull and body > 0:
                return PAT_ENGULFING, direction, min(_PERCENT, body_ratio * _PERCENT)
            if h > prev["high"] and low < prev["low"]:
                return PAT_OUTSIDE, direction, min(_PERCENT, body_ratio * _PERCENT)
            if h < prev["high"] and low > prev["low"]:
                return PAT_INSIDE, DIR_NONE, round((1.0 - body_ratio) * _PERCENT)
        if body_ratio >= self._marubozu_body:
            return PAT_MARUBOZU, direction, round(body_ratio * _PERCENT)
        if body_ratio <= self._pin_body and max(upper, lower) >= self._pin_wick * rng:
            pin_dir = DIR_BULL if lower > upper else DIR_BEAR
            return PAT_PIN, pin_dir, round(max(upper, lower) / rng * _PERCENT)
        if body_ratio <= self._doji_body:
            return PAT_DOJI, DIR_NONE, round((1.0 - body_ratio) * _PERCENT)
        return PAT_NONE, DIR_NONE, 0.0

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, o: float,
                    h: float, low: float, c: float, prev: dict[str, float] | None) -> None:
        if self._context is None:
            return
        base = {"symbol": symbol, "id": "candle", "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe}
        if prev is None:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": PAT_NONE,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        pattern, direction, strength = self._detect(o, h, low, c, prev)
        rng = h - low
        body_ratio = abs(c - o) / rng if rng > 0 else 0.0
        score = int(round(strength))
        confidence = round(strength / _PERCENT, 2)
        meta.update({"pattern": pattern, "direction": direction,
                     "body_ratio": round(body_ratio, 3),
                     "upper_wick": round(h - max(o, c), 5),
                     "lower_wick": round(min(o, c) - low, 5)})
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": pattern, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES,
                                details={"tracked": len(self._prev)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d tracked=%d" % (
                self._candles_seen, self._emitted, len(self._prev)),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "tracked": len(self._prev)})
