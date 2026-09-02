from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.1"

EVENT_IN = "feed.ctrader.tick"
EVENT_OUT = "market.reference"
PROVIDER = "ctrader"

REASON_NOT_STARTED = "NOT_STARTED"


def _price(payload: dict[str, Any]) -> float | None:
    for field in ("price", "bid"):
        try:
            value = float(payload.get(field))
        except (TypeError, ValueError):
            continue
        if value == value and value > 0:
            return value
    return None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._map: dict[str, str] = {}
        self._forwarded = 0
        self._last_symbol = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        raw = context.config.get("symbol_map")
        self._map = {str(k).strip().upper(): str(v).strip()
                     for k, v in (raw or {}).items()
                     if str(k).strip() and str(v).strip()}
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
        reference = self._map.get(str(payload.get("s") or payload.get("symbol") or "").strip().upper())
        if not reference:
            return
        value = _price(payload)
        if value is None:
            return
        self._forwarded += 1
        self._last_symbol = reference
        await self._context.publish(EVENT_OUT, {
            "provider": PROVIDER, "symbol": reference, "value": value})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"forwarded": self._forwarded, "last_symbol": self._last_symbol,
                   "symbol_map": dict(self._map)}
        if self._forwarded == 0:
            return HealthStatus(
                state=HealthState.HEALTHY,
                message="READY_AWAITING_FIRST_CTRADER_REFERENCE_TICK | forwarded=0",
                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="forwarded=%d last=%s" % (self._forwarded, self._last_symbol),
            details=details)
