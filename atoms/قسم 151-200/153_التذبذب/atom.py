from __future__ import annotations

import math
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
EVENT_OUT = "analysis.volatility.state"

METHOD = "atr_relative"

LEVEL_LOW = "low"
LEVEL_NORMAL = "normal"
LEVEL_HIGH = "high"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_PERCENT = 100.0
_SCORE_MAX = 100.0
_ATR_PCT_SCALE = 120.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / len(values))


@section_atom("150", "153")
@live_analyzer("volatility", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._atr_window = 14
        self._baseline_window = 50
        self._stddev_window = 20
        self._high_mult = 1.5
        self._low_mult = 0.6
        self._min_candles = 20
        self._state: dict[tuple, dict[str, Any]] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._atr_window = int(cfg["atr_window"])
        self._baseline_window = int(cfg["baseline_window"])
        self._stddev_window = int(cfg["stddev_window"])
        self._high_mult = float(cfg["high_mult"])
        self._low_mult = float(cfg["low_mult"])
        self._min_candles = int(cfg["min_candles"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping this atom's true-range and close history on
    # every restart. Measured effect: `min_candles` (20 by default) closed
    # candles are required before it leaves insufficient_data, so a reboot
    # silenced volatility for 20 candles every time. The wrapper now chains
    # both states -- "live_analysis" for its own tick memory, "atom" for
    # this history -- so this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "scopes": [{"symbol": symbol, "timeframe": timeframe,
                        "tr": list(st["tr"]), "closes": list(st["closes"]),
                        "prev_close": st["prev_close"]}
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
            tr: deque = deque(maxlen=self._baseline_window * 5)
            for value in row.get("tr") or []:
                number = _to_float(value)
                if number is not None:
                    tr.append(number)
            closes: deque = deque(maxlen=self._baseline_window * 5)
            for value in row.get("closes") or []:
                number = _to_float(value)
                if number is not None:
                    closes.append(number)
            self._state[(symbol, str(row.get("timeframe") or ""))] = {
                "tr": tr, "closes": closes,
                "prev_close": _to_float(row.get("prev_close")),
            }

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        high = _to_float(payload.get("high"))
        low = _to_float(payload.get("low"))
        close = _to_float(payload.get("close"))
        if not symbol or high is None or low is None or close is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        st = self._state.get(key)
        if st is None:
            st = {"tr": deque(maxlen=self._baseline_window * 5),
                  "closes": deque(maxlen=self._baseline_window * 5), "prev_close": None}
            self._state[key] = st
        prev_close = st["prev_close"]
        if prev_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        st["tr"].append(true_range)
        st["closes"].append(close)
        st["prev_close"] = close
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, close, high, low, st,
                         str(payload.get("account_id") or ""))

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, close: float,
                    high: float, low: float, st: dict[str, Any],
                    account: str = "") -> None:
        if self._context is None:
            return
        base = {"symbol": symbol, "id": "volatility", "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe}
        w_base = _speed_window(self._baseline_window, symbol, account, 5)
        w_atr = _speed_window(self._atr_window, symbol, account, 3)
        w_std = _speed_window(self._stddev_window, symbol, account, 5)
        tr = list(st["tr"])[-w_base:]
        w_min = _speed_window(self._min_candles, symbol, account, 6)
        if len(tr) < w_min:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": LEVEL_NORMAL,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        current_atr = _mean(tr[-w_atr:])
        baseline_atr = _mean(tr)
        ratio = current_atr / baseline_atr if baseline_atr > 0 else 1.0
        if ratio >= self._high_mult:
            level = LEVEL_HIGH
        elif ratio <= self._low_mult:
            level = LEVEL_LOW
        else:
            level = LEVEL_NORMAL
        atr_pct = current_atr / close * _PERCENT if close > 0 else 0.0
        score = int(round(min(_SCORE_MAX, atr_pct * _ATR_PCT_SCALE)))
        confidence = round(min(1.0, len(tr) / w_base), 2)
        stddev = _stddev(list(st["closes"])[-w_std:])
        meta.update({"atr": round(current_atr, 5), "atr_pct": round(atr_pct, 4),
                     "baseline_atr": round(baseline_atr, 5), "ratio": round(ratio, 2),
                     "range": round(high - low, 5), "stddev": round(stddev, 5)})
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": level, "score": score,
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
            message="candles=%d emitted=%d tracked=%d" % (
                self._candles_seen, self._emitted, len(self._state)),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "tracked": len(self._state)})
