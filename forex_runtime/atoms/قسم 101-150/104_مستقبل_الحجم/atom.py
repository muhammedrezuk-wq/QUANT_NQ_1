from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.0"

EVENT_IN = "market.volume"
EVENT_OUT = "market_data.volume_received"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "NO_VOLUME_YET"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self.received_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_volume)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_volume(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        out: dict[str, Any] = {"symbol": symbol, "volume": _to_float(payload.get("volume"))}
        ts = payload.get("timestamp")
        if isinstance(ts, (int, float)):
            out["timestamp"] = ts
        provider = payload.get("provider")
        if provider is not None:
            out["provider"] = provider
        self.received_count += 1
        await self._context.publish(EVENT_OUT, out)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self.received_count == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA)
        return HealthStatus(
            state=HealthState.HEALTHY, message="received=%d" % self.received_count)
