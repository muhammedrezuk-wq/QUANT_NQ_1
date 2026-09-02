from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.0"

EVENT_IN = "market.tick.validated"
EVENT_OUT = "market_data.spread_updated"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "NO_TICKS_YET"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self.updates_count = 0
        self.last_spread: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
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
        bid = _to_float(payload.get("bid"))
        ask = _to_float(payload.get("ask"))
        if not symbol or bid is None or ask is None:
            return
        spread = ask - bid
        self.last_spread = spread
        self.updates_count += 1
        out: dict[str, Any] = {"symbol": symbol, "spread": spread,
                               "account_id": payload.get("account_id"),
                               "broker": payload.get("broker"),
                               "provider": payload.get("provider")}
        ts = payload.get("timestamp")
        if isinstance(ts, (int, float)):
            out["timestamp"] = ts
        await self._context.publish(EVENT_OUT, out)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self.updates_count == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="updates=%d last_spread=%s" % (self.updates_count, self.last_spread))
