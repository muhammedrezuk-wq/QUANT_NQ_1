from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.1"

EVENT_IN = "structure.swing.state"
EVENT_OUT = "liquidity.pool.state"

METHOD = "swing_as_pool"
ID_POOL = "pool"

SWING_HIGH = "swing_high"
SWING_LOW = "swing_low"

POOL_HIGH = "pool_high"
POOL_LOW = "pool_low"
SIGNAL_NONE = "none"

SIDE_HIGH = "high"
SIDE_LOW = "low"

STATUS_OK = "ok"
QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_SWING_INPUT_YET"

_PRICE_DP = 4
_CONF_DP = 4
# Confidence derives from swing prominence (0..100), not a binary 1.0/0.0.
_SCORE_MAX = 100.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _round(value: Any) -> Any:
    return round(value, _PRICE_DP) if value is not None else None


def _confidence_from_score(score: Any) -> float:
    value = _to_float(score)
    if value is None:
        return 0.0
    return round(max(0.0, min(1.0, value / _SCORE_MAX)), _CONF_DP)


@section_atom("250", "251")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._inputs_seen = 0
        self._pools = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_swing)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_swing(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        self._inputs_seen += 1
        cycle_id = str(payload.get("cycle_id", ""))
        meta_in = payload.get("metadata") or {}
        timeframe = str(payload.get("timeframe", "") or meta_in.get("timeframe", ""))
        close = _to_float(meta_in.get("close"))
        swing_signal = payload.get("signal")
        swing_price = _to_float(meta_in.get("price"))
        swing_time = meta_in.get("swing_time")
        swing_score = payload.get("score", 0)
        signal = SIGNAL_NONE
        side = None
        price = None
        score = 0
        if swing_signal == SWING_HIGH and swing_price is not None:
            signal = POOL_HIGH
            side = SIDE_HIGH
            price = swing_price
            score = swing_score
            self._pools += 1
        elif swing_signal == SWING_LOW and swing_price is not None:
            signal = POOL_LOW
            side = SIDE_LOW
            price = swing_price
            score = swing_score
            self._pools += 1
        await self._emit(symbol, timeframe, cycle_id, close, signal, side, price,
                         swing_time, score)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, close: Any,
                    signal: str, side: Any, price: Any, pool_time: Any,
                    score: Any) -> None:
        if self._context is None:
            return
        confidence = _confidence_from_score(score) if signal != SIGNAL_NONE else 0.0
        meta = {"method": METHOD, "timeframe": timeframe, "side": side,
                "price": _round(price), "pool_time": pool_time, "close": close}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_POOL, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": int(score),
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._inputs_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="inputs=%d pools=%d emitted=%d" % (
                self._inputs_seen, self._pools, self._emitted),
            details={"inputs": self._inputs_seen, "pools": self._pools,
                     "emitted": self._emitted})
