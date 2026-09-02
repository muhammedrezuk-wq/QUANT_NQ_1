from __future__ import annotations

import math
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of
from shared.atom_evidence import window_evidence

WEIGHT = 5.882353
ATOM_VERSION = "1.2.0"

EVENT_IN = "market.tick.validated"
EVENT_OUT = "stats.period_compare.state"

METHOD = "rolling_half_compare"
ID_PERIOD = "period_compare"

SIGNAL_RISING = "rising"
SIGNAL_FALLING = "falling"
SIGNAL_STABLE = "stable"

VOL_EXPANDING = "expanding"
VOL_CONTRACTING = "contracting"
VOL_STABLE = "stable"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_data_points"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TICKS = "NO_TICKS_YET"

_MIN_POINTS = 4
_VALUE_DP = 6


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _mean_std(vals: list) -> tuple:
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    mean = sum(vals) / n
    if n < 2:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in vals) / (n - 1)
    return mean, math.sqrt(variance)


@section_atom("300", "318")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_size = 20
        self._drift_band = 0.001
        self._vol_band = 0.1
        self._state: dict[tuple, deque] = {}
        self._ticks_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._window_size = int(context.config["window_size"])
        self._drift_band = float(context.config["drift_band"])
        self._vol_band = float(context.config["vol_band"])
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

    def _vol_state(self, ostd: float, rstd: float) -> tuple:
        if ostd <= 0.0:
            ratio = 0.0
            state = VOL_EXPANDING if rstd > 0.0 else VOL_STABLE
            return state, ratio
        ratio = rstd / ostd
        if ratio >= 1.0 + self._vol_band:
            state = VOL_EXPANDING
        elif ratio <= 1.0 - self._vol_band:
            state = VOL_CONTRACTING
        else:
            state = VOL_STABLE
        return state, ratio

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    window: deque) -> None:
        if self._context is None:
            return
        count = len(window)
        base = {"symbol": symbol, "id": ID_PERIOD, "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe, "window": self._window_size,
                "count": count}
        if count < _MIN_POINTS:
            await self._context.publish(EVENT_OUT, {
                **window_evidence(have=len(window), need=self._window_size),
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_STABLE,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        data = list(window)
        mid = count // 2
        older = data[:mid]
        recent = data[mid:]
        omean, ostd = _mean_std(older)
        rmean, rstd = _mean_std(recent)
        drift = (rmean - omean) / abs(omean) if omean != 0 else 0.0
        if drift >= self._drift_band:
            signal = SIGNAL_RISING
        elif drift <= -self._drift_band:
            signal = SIGNAL_FALLING
        else:
            signal = SIGNAL_STABLE
        vol_state, vol_ratio = self._vol_state(ostd, rstd)
        confidence = round(count / self._window_size, 2) if self._window_size > 0 else 0.0
        meta.update({"older_mean": round(omean, _VALUE_DP), "recent_mean": round(rmean, _VALUE_DP),
                     "drift": round(drift, _VALUE_DP), "older_std": round(ostd, _VALUE_DP),
                     "recent_std": round(rstd, _VALUE_DP), "vol_ratio": round(vol_ratio, _VALUE_DP),
                     "vol_state": vol_state})
        await self._context.publish(EVENT_OUT, {
            **window_evidence(have=len(window), need=self._window_size),
            **base, "status": STATUS_OK, "signal": signal, "score": 0,
            "confidence": min(100.0, confidence * 100.0), "quality": QUALITY_GOOD, "weight": WEIGHT,
            "analysis_state": "READY", "ready": True,
            "warnings": [], "metadata": meta})
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
