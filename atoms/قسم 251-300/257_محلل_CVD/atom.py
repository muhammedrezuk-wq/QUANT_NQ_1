from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.0"

EVENT_IN = "liquidity.delta.state"
EVENT_OUT = "liquidity.cvd.state"

METHOD = "cumulative_delta"
ID_CVD = "cvd"

SIGNAL_RISING = "rising"
SIGNAL_FALLING = "falling"
SIGNAL_FLAT = "flat"

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


@section_atom("250", "257")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._cvd: dict[tuple, float] = {}
        self._inputs_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_delta)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

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
        key = (symbol, timeframe)
        cvd = self._cvd.get(key, 0.0) + delta
        self._cvd[key] = cvd
        if delta > 0:
            signal = SIGNAL_RISING
        elif delta < 0:
            signal = SIGNAL_FALLING
        else:
            signal = SIGNAL_FLAT
        # Confidence derives from upstream delta ratio (|ratio|), not a
        # fabricated constant; missing ratio (source other than 256) -> 0.
        ratio_in = _to_float(meta_in.get("ratio"))
        confidence = round(min(1.0, abs(ratio_in)), _DP) if ratio_in is not None else 0.0
        meta = {"method": METHOD, "timeframe": timeframe, "cvd": round(cvd, _DP),
                "delta": round(delta, _DP)}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_CVD, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": 0, "confidence": confidence,
            "quality": QUALITY_GOOD, "warnings": [], "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._inputs_seen == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_UNAVAILABLE,
                details={"note": "needs 256 delta (which needs real order flow 106/107)"})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="inputs=%d emitted=%d tracked=%d" % (
                self._inputs_seen, self._emitted, len(self._cvd)),
            details={"inputs": self._inputs_seen, "emitted": self._emitted,
                     "tracked": len(self._cvd)})
