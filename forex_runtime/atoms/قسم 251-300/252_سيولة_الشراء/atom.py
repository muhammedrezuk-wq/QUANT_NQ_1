from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.0"

EVENT_IN = "liquidity.pool.state"
EVENT_OUT = "liquidity.buyside.state"

METHOD = "pool_side_filter"
ID_BUYSIDE = "buyside"

POOL_HIGH = "pool_high"

SIGNAL_BUYSIDE = "buyside"
SIGNAL_NONE = "none"

SIDE_HIGH = "high"

STATUS_OK = "ok"
QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_POOL_INPUT_YET"

_CONF_DP = 4
# Confidence derives from the pool's prominence score (0..100), not binary 1.0/0.0.
_SCORE_MAX = 100.0


def _confidence_from_score(score: Any) -> float:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, value / _SCORE_MAX)), _CONF_DP)


@section_atom("250", "252")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._inputs_seen = 0
        self._pools = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_pool)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_pool(self, payload: dict[str, Any]) -> None:
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
        close = meta_in.get("close")
        is_buyside = payload.get("signal") == POOL_HIGH
        signal = SIGNAL_BUYSIDE if is_buyside else SIGNAL_NONE
        price = meta_in.get("price") if is_buyside else None
        pool_time = meta_in.get("pool_time") if is_buyside else None
        score = int(payload.get("score", 0)) if is_buyside else 0
        confidence = _confidence_from_score(score) if is_buyside else 0.0
        if is_buyside:
            self._pools += 1
        meta = {"method": METHOD, "timeframe": timeframe, "side": SIDE_HIGH,
                "price": price, "pool_time": pool_time, "close": close}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_BUYSIDE, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": score,
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
            message="inputs=%d buyside=%d emitted=%d" % (
                self._inputs_seen, self._pools, self._emitted),
            details={"inputs": self._inputs_seen, "buyside": self._pools,
                     "emitted": self._emitted})
