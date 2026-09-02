from __future__ import annotations

import math
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.1.1"
EVENT_IN = "market.tick.validated"
EVENT_OUT = "market_data.price_received"


def _number(value):
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self.received_count = 0
        self.dropped_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_tick)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"received": self.received_count, "dropped": self.dropped_count}
        if self.received_count == 0: return HealthStatus(state=HealthState.DEGRADED, message="NO_TICKS_YET", details=details)
        state = HealthState.DEGRADED if self.dropped_count else HealthState.HEALTHY
        return HealthStatus(state=state, message=f"received={self.received_count} dropped={self.dropped_count}", details=details)

    async def _on_tick(self, payload: dict) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        symbol = str(payload.get("symbol") or "")
        account_id = str(payload.get("account_id") or "")
        bid = _number(payload.get("bid")); ask = _number(payload.get("ask"))
        if not symbol or bid is None or ask is None or bid <= 0 or ask < bid:
            self.dropped_count += 1
            return
        out = {"account_id": account_id or None, "symbol": symbol, "bid": bid, "ask": ask,
               "price": _number(payload.get("price")) or (bid + ask) / 2.0}
        for name in ("timestamp", "exchange_timestamp", "received_at", "provider", "broker", "source_row_id"):
            if payload.get(name) is not None: out[name] = payload[name]
        self.received_count += 1
        await self._context.publish(EVENT_OUT, out)
