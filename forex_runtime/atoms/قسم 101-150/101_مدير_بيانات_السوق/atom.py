from __future__ import annotations

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus


ATOM_VERSION = "2.1.0"

def _ts_from(payload: dict) -> dict:
    ts = payload.get("timestamp")
    return {"timestamp": ts} if isinstance(ts, (int, float)) else {}


_SOURCE_EVENTS = {
    "market.tick.validated": "tick",
    "market_data.price_received": "price",
    "market_data.candle_closed": "candle",
    "market_data.volume_received": "volume",
    "market_data.spread_updated": "spread",
    "market_data.depth_updated": "depth",
    "market_data.trade_tape_updated": "trade_tape",
    "market_data.news_received": "news",
    "market_data.calendar_event": "calendar",
    "market_data.reference_index_updated": "reference_index",
}

_UNIFIED_EVENT = "market_data.state"


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._state: dict[str, dict[str, dict]] = {}
        self.unified_published_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        for event_name, field_key in _SOURCE_EVENTS.items():
            context.subscribe(event_name, self._make_collector(field_key))
        context.logger.info("101 initialized: %d source events", len(_SOURCE_EVENTS))

    async def start(self) -> None:
        self._running = True
        if self._context is not None:
            self._context.logger.info("101 started")

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        pass

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY,
            message=f"symbols={len(self._state)} unified_published={self.unified_published_count}",
        )

    def _make_collector(self, field_key: str):
        async def handler(payload: dict) -> None:
            if not self._running or self._context is None:
                return
            symbol = str(payload.get("symbol", "_global"))
            symbol_state = self._state.setdefault(symbol, {})
            symbol_state[field_key] = payload
            self.unified_published_count += 1
            await self._context.publish(
                _UNIFIED_EVENT,
                {
                    "symbol": symbol,
                    "updated_field": field_key,
                    "state": dict(symbol_state),
                    **_ts_from(payload),
                },
            )

        return handler
