from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of
from shared.atom_evidence import window_evidence

WEIGHT = 5.882353
ATOM_VERSION = "1.3.0"

EVENT_IN = "market.tick.validated"
EVENT_OUT = "stats.mean.state"

METHOD = "rolling_mean"
ID_MEAN = "mean"

SIGNAL_COMPUTED = "computed"

STATUS_OK = "ok"

QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TICKS = "NO_TICKS_YET"

_VALUE_DP = 6


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("300", "301")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_size = 20
        self._state: dict[tuple, deque] = {}
        self._ticks_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._window_size = int(context.config["window_size"])
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

    def _analysis_maturity(self, window: deque) -> float | None:
        """Maturity distinct from data_completeness: converges as the window's second-half mean approaches its first-half mean; None before the window fills."""
        count = len(window)
        if count < self._window_size or count < 2:
            return None
        values = list(window)
        half = count // 2
        first_half, second_half = values[:half], values[half:]
        mean_first = sum(first_half) / len(first_half)
        mean_second = sum(second_half) / len(second_half)
        divergence = abs(mean_second - mean_first)
        value_range = max(values) - min(values)
        if value_range <= 0:
            return 100.0
        return max(0.0, min(100.0, (1.0 - divergence / value_range) * 100.0))

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    window: deque) -> None:
        if self._context is None:
            return
        count = len(window)
        mean = sum(window) / count
        confidence = round(count / self._window_size, 2) if self._window_size > 0 else 0.0
        meta = {"method": METHOD, "timeframe": timeframe, "window": self._window_size,
                "count": count, "value": round(mean, _VALUE_DP)}
        evidence = window_evidence(have=len(window), need=self._window_size)
        maturity = self._analysis_maturity(window)
        if maturity is not None:
            evidence["current_depth"] = round(maturity, 4)
        await self._context.publish(EVENT_OUT, {
            **evidence,
            "symbol": symbol, "id": ID_MEAN, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": SIGNAL_COMPUTED, "score": 0,
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
