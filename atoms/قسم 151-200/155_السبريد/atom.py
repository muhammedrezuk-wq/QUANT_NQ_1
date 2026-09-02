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
EVENT_OUT = "analysis.spread.state"

METHOD = "range_expansion"

SIGNAL_EXPANSION = "expansion"
SIGNAL_CONTRACTION = "contraction"
SIGNAL_STABLE = "stable"

SIZE_WIDE = "wide"
SIZE_NARROW = "narrow"
SIZE_NORMAL = "normal"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_SCORE_MAX = 100.0
_EXPANSION_SCALE = 100.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@section_atom("150", "155")
@live_analyzer("spread", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._baseline_window = 50
        self._exp_short = 5
        self._exp_long = 20
        self._exp_mult = 1.3
        self._wide_mult = 1.5
        self._narrow_mult = 0.6
        self._min_candles = 20
        self._ranges: dict[tuple, deque] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._baseline_window = int(cfg["baseline_window"])
        self._exp_short = int(cfg["exp_short"])
        self._exp_long = int(cfg["exp_long"])
        self._exp_mult = float(cfg["exp_mult"])
        self._wide_mult = float(cfg["wide_mult"])
        self._narrow_mult = float(cfg["narrow_mult"])
        self._min_candles = int(cfg["min_candles"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping this atom's own range history on every
    # restart. Measured effect: `min_candles` (20 by default) closed candles
    # are required before it leaves insufficient_data, so a reboot silenced
    # spread for 20 candles every time. The wrapper now chains both states --
    # "live_analysis" for its own tick memory, "atom" for this history -- so
    # this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "scopes": [{"symbol": symbol, "timeframe": timeframe,
                        "ranges": list(ranges)}
                       for (symbol, timeframe), ranges in self._ranges.items()],
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._candles_seen = int(state.get("candles_seen") or 0)
        self._emitted = int(state.get("emitted") or 0)
        self._ranges = {}
        for row in state.get("scopes") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            ranges: deque = deque(maxlen=self._baseline_window * 5)
            for value in row.get("ranges") or []:
                number = _to_float(value)
                if number is not None:
                    ranges.append(number)
            if ranges:
                self._ranges[(symbol, str(row.get("timeframe") or ""))] = ranges

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        high = _to_float(payload.get("high"))
        low = _to_float(payload.get("low"))
        if not symbol or high is None or low is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        ranges = self._ranges.get(key)
        if ranges is None:
            ranges = deque(maxlen=self._baseline_window * 5)
            self._ranges[key] = ranges
        ranges.append(max(0.0, high - low))
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, ranges,
                         str(payload.get("account_id") or ""))

    def _size(self, current: float, baseline: float) -> str:
        if baseline <= 0:
            return SIZE_NORMAL
        if current > baseline * self._wide_mult:
            return SIZE_WIDE
        if current < baseline * self._narrow_mult:
            return SIZE_NARROW
        return SIZE_NORMAL

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    ranges: deque, account: str = "") -> None:
        if self._context is None:
            return
        base = {"symbol": symbol, "id": "spread", "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe}
        w_base = _speed_window(self._baseline_window, symbol, account, 5)
        vals = list(ranges)[-w_base:]
        w_min = _speed_window(self._min_candles, symbol, account, 6)
        if len(vals) < w_min:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_STABLE,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        current = vals[-1]
        short_avg = _mean(vals[-_speed_window(self._exp_short, symbol, account, 2):])
        long_avg = _mean(vals[-_speed_window(self._exp_long, symbol, account, 6):])
        baseline = _mean(vals)
        exp_ratio = short_avg / long_avg if long_avg > 0 else 1.0
        if exp_ratio > self._exp_mult:
            signal = SIGNAL_EXPANSION
        elif exp_ratio < 1.0 / self._exp_mult:
            signal = SIGNAL_CONTRACTION
        else:
            signal = SIGNAL_STABLE
        score = 0 if signal == SIGNAL_STABLE else int(round(
            min(_SCORE_MAX, abs(exp_ratio - 1.0) * _EXPANSION_SCALE)))
        confidence = round(min(1.0, len(vals) / w_base), 2)
        meta.update({"range": round(current, 5), "baseline_range": round(baseline, 5),
                     "expansion_ratio": round(exp_ratio, 2),
                     "size": self._size(current, baseline)})
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES,
                                details={"tracked": len(self._ranges)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d tracked=%d" % (
                self._candles_seen, self._emitted, len(self._ranges)),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "tracked": len(self._ranges)})
