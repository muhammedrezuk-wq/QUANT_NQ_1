from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.analysis_speed import speed_factor, speed_value
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"


def _speed_window(base: int, symbol: str, account: str, floor: int) -> int:
    """نافذة مشتقة من مفتاح سرعة التحليل — «جماعة الشموع» بنفس المفتاح (٢٦-٠٨).

    الأساس = قيمة المانيفست (نقطة التطابق 50 = اليوم حرفيًّا) · السقف الأساس×5."""
    factor = speed_factor(speed_value(account, symbol))
    return max(floor, min(base * 5, int(round(base * factor))))

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.acceleration.state"

METHOD = "self_calibrated_speed_change"

LEVEL_ACCEL = "accelerating"
LEVEL_DECEL = "decelerating"
LEVEL_STEADY = "steady"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_PERCENT = 100.0
_SCORE_MAX = 100.0
_SCORE_SCALE = 50.0
_CONF_SIGNAL = 0.7
_CONF_STEADY = 0.4
_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _mean(values: Any) -> float:
    return sum(values) / len(values) if values else 0.0


@section_atom("150", "163")
@live_analyzer("acceleration", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._baseline_window = 20
        self._accel_ratio = 0.5
        self._state: dict[tuple, dict[str, Any]] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._baseline_window = int(cfg["baseline_window"])
        self._accel_ratio = float(cfg["accel_ratio"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping this atom's own speed-baseline and previous-
    # speed history on every restart. Measured effect: `baseline_window`
    # (20 by default) closed candles plus one prior speed reading are
    # required before it leaves insufficient_data, so a reboot silenced
    # acceleration for 20+ candles every time. The wrapper now chains both
    # states -- "live_analysis" for its own tick memory, "atom" for this
    # history -- so this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "scopes": [{"symbol": symbol, "timeframe": timeframe,
                        "prev_close": st["prev_close"], "prev_speed": st["prev_speed"],
                        "speeds": list(st["speeds"])}
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
            prev_close = _to_float(row.get("prev_close"))
            if prev_close is None:
                continue
            speeds: deque = deque(maxlen=self._baseline_window * 5)
            for value in row.get("speeds") or []:
                number = _to_float(value)
                if number is not None:
                    speeds.append(number)
            self._state[(symbol, str(row.get("timeframe") or ""))] = {
                "prev_close": prev_close, "prev_speed": _to_float(row.get("prev_speed")),
                "speeds": speeds,
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
            self._state[key] = {"prev_close": close, "prev_speed": None,
                                "speeds": deque(maxlen=self._baseline_window * 5)}
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        prev_close = st["prev_close"]
        st["prev_close"] = close
        speed = abs(close - prev_close) / prev_close * _PERCENT if prev_close else 0.0
        prev_speed = st["prev_speed"]
        st["prev_speed"] = speed
        speeds = st["speeds"]
        w_base = _speed_window(self._baseline_window, symbol,
                               str(payload.get("account_id") or ""), 5)
        baseline = _mean(list(speeds)[-w_base:])
        speeds.append(speed)
        if prev_speed is None or len(speeds) < w_base or baseline <= 0.0:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        ratio = (speed - prev_speed) / baseline
        if ratio > self._accel_ratio:
            signal = LEVEL_ACCEL
        elif ratio < -self._accel_ratio:
            signal = LEVEL_DECEL
        else:
            signal = LEVEL_STEADY
        score = int(round(min(_SCORE_MAX, abs(ratio) * _SCORE_SCALE)))
        confidence = _CONF_STEADY if signal == LEVEL_STEADY else _CONF_SIGNAL
        await self._emit(symbol, timeframe, cycle_id, signal, score, confidence,
                         speed, prev_speed, baseline, ratio)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, signal: str,
                    score: int, confidence: float, speed: float, prev_speed: float,
                    baseline: float, ratio: float) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "acceleration", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe,
                         "speed_pct": round(speed, _DP),
                         "prev_speed_pct": round(prev_speed, _DP),
                         "baseline_speed": round(baseline, _DP),
                         "ratio": round(ratio, _DP)}})
        self._emitted += 1

    async def _emit_insufficient(self, symbol: str, timeframe: str,
                                 cycle_id: str) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "acceleration", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_INSUFFICIENT, "signal": LEVEL_STEADY, "score": 0,
            "confidence": 0.0, "quality": QUALITY_LOW, "warnings": [WARN_INSUFFICIENT],
            "metadata": {"method": METHOD, "timeframe": timeframe}})
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
