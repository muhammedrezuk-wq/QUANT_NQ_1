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
EVENT_OUT = "stats.outlier.state"

METHOD = "rolling_iqr_outlier"
ID_OUTLIER = "outlier"

SIGNAL_OUTLIER = "outlier"
SIGNAL_NORMAL = "normal"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_data_points"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TICKS = "NO_TICKS_YET"

_MIN_POINTS = 4
_QUARTER = 4
_VALUE_DP = 6


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("300", "313")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_size = 20
        self._multiplier = 1.5
        self._state: dict[tuple, deque] = {}
        self._ticks_seen = 0
        self._outliers = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._window_size = int(context.config["window_size"])
        self._multiplier = float(context.config["iqr_multiplier"])
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
        base = {"symbol": symbol, "id": ID_OUTLIER, "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe, "window": self._window_size,
                "count": count, "multiplier": self._multiplier}
        if count < _MIN_POINTS:
            await self._context.publish(EVENT_OUT, {
                **window_evidence(have=len(window), need=self._window_size),
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_NORMAL,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        srt = sorted(window)
        q1 = srt[count // _QUARTER]
        q3 = srt[(count * (_QUARTER - 1)) // _QUARTER]
        iqr = q3 - q1
        lower = q1 - self._multiplier * iqr
        upper = q3 + self._multiplier * iqr
        last = window[-1]
        is_outlier = last < lower or last > upper
        signal = SIGNAL_OUTLIER if is_outlier else SIGNAL_NORMAL
        if is_outlier:
            self._outliers += 1
        meta.update({"value": round(last, _VALUE_DP), "q1": round(q1, _VALUE_DP),
                     "q3": round(q3, _VALUE_DP), "lower": round(lower, _VALUE_DP),
                     "upper": round(upper, _VALUE_DP)})
        await self._context.publish(EVENT_OUT, {
            **window_evidence(have=len(window), need=self._window_size),
            **base, "status": STATUS_OK, "signal": signal, "score": 0,
            "confidence": 1.0 if is_outlier else 0.0, "quality": QUALITY_GOOD, "weight": WEIGHT,
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
            message="ticks=%d outliers=%d emitted=%d tracked=%d" % (
                self._ticks_seen, self._outliers, self._emitted, len(self._state)),
            details={"ticks": self._ticks_seen, "outliers": self._outliers,
                     "emitted": self._emitted, "tracked": len(self._state)})
