from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.0"

EVENT_DELTA = "liquidity.delta.state"
EVENT_CANDLE = "market_data.candle_closed"
EVENT_OUT = "liquidity.absorption.state"

METHOD = "volume_no_movement"
ID_ABSORPTION = "absorption"

SIGNAL_ABSORBED = "absorbed"
SIGNAL_NORMAL = "normal"

STATUS_OK = "ok"
QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_UNAVAILABLE = "ORDER_FLOW_UNAVAILABLE"

_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("250", "258")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._min_ratio = 0.0
        self._range: dict[tuple, float] = {}
        self._inputs_seen = 0
        self._absorbed = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._min_ratio = float(context.config["absorption_ratio"])
        context.subscribe(EVENT_CANDLE, self._on_candle)
        context.subscribe(EVENT_DELTA, self._on_delta)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        high = _to_float(payload.get("high"))
        low = _to_float(payload.get("low"))
        if not symbol or high is None or low is None:
            return
        key = (str(symbol), str(payload.get("timeframe", "")))
        self._range[key] = high - low

    async def _on_delta(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        self._inputs_seen += 1
        meta_in = payload.get("metadata") or {}
        delta = _to_float(meta_in.get("delta"))
        if delta is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", "") or meta_in.get("timeframe", ""))
        cycle_id = str(payload.get("cycle_id", ""))
        rng = self._range.get((symbol, timeframe))
        signal = SIGNAL_NORMAL
        ratio = 0.0
        if rng is not None and rng > 0:
            ratio = abs(delta) / rng
            if ratio >= self._min_ratio:
                signal = SIGNAL_ABSORBED
                self._absorbed += 1
        meta = {"method": METHOD, "timeframe": timeframe, "delta": round(delta, _DP),
                "range": round(rng, _DP) if rng is not None else None,
                "ratio": round(ratio, _DP)}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_ABSORPTION, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal,
            "confidence": 1.0 if signal == SIGNAL_ABSORBED else 0.0,
            "quality": QUALITY_GOOD, "warnings": [], "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._inputs_seen == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_UNAVAILABLE,
                details={"note": "needs 256 delta (real order flow 106/107)"})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="inputs=%d absorbed=%d emitted=%d" % (
                self._inputs_seen, self._absorbed, self._emitted),
            details={"inputs": self._inputs_seen, "absorbed": self._absorbed,
                     "emitted": self._emitted})
