from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.0"

EVENT_IN = "market_data.trade_tape_updated"
EVENT_OUT = "liquidity.delta.state"

METHOD = "order_flow_delta"
ID_DELTA = "delta"

FIELD_BUY = "buy_volume"
FIELD_SELL = "sell_volume"

SIGNAL_BUY = "buy_pressure"
SIGNAL_SELL = "sell_pressure"
SIGNAL_BALANCED = "balanced"

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


def _volumes(payload: dict[str, Any]) -> tuple:
    buy = _to_float(payload.get(FIELD_BUY))
    sell = _to_float(payload.get(FIELD_SELL))
    if buy is None or sell is None:
        meta = payload.get("metadata") or {}
        buy = _to_float(meta.get(FIELD_BUY))
        sell = _to_float(meta.get(FIELD_SELL))
    return buy, sell


@section_atom("250", "256")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._inputs_seen = 0
        self._computed = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_trade)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        self._inputs_seen += 1
        buy, sell = _volumes(payload)
        if buy is None or sell is None:
            return
        symbol = str(symbol)
        meta_in = payload.get("metadata") or {}
        timeframe = str(payload.get("timeframe", "") or meta_in.get("timeframe", ""))
        cycle_id = str(payload.get("cycle_id", ""))
        delta = buy - sell
        total = buy + sell
        ratio = delta / total if total > 0 else 0.0
        if delta > 0:
            signal = SIGNAL_BUY
        elif delta < 0:
            signal = SIGNAL_SELL
        else:
            signal = SIGNAL_BALANCED
        self._computed += 1
        meta = {"method": METHOD, "timeframe": timeframe, "delta": round(delta, _DP),
                "ratio": round(ratio, _DP)}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_DELTA, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal,
            "confidence": abs(round(ratio, _DP)), "quality": QUALITY_GOOD,
            "warnings": [], "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._computed == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_UNAVAILABLE,
                details={"inputs": self._inputs_seen,
                         "note": "needs real buy/sell volume from 106/107"})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="computed=%d emitted=%d" % (self._computed, self._emitted),
            details={"inputs": self._inputs_seen, "computed": self._computed,
                     "emitted": self._emitted})
