from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "4.2.0"

EVENT_OUT = "market_data.cleaned"
REASON_NOT_STARTED = "NOT_STARTED"

EVENTS = (
    "market.tick.validated",
    "market_data.price_received",
    "market_data.spread_updated",
    "market_data.candle_closed",
    "market_data.volume_received",
    "market_data.depth_updated",
    "market_data.trade_tape_updated",
    "market_data.news_received",
    "market_data.calendar_event",
    "market_data.reference_index_updated",
)

STATUS_SIDE_ONLY = "SIDE_ONLY"
FALLBACK_SOURCE = "side.legacy"


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._last_seen: dict[tuple, dict] = {}
        self.processed_count = 0
        self.duplicates_dropped = 0
        self.rejected_envelopes = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        for event in EVENTS:
            context.subscribe(event, self._handler(event))

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _handler(self, event: str):
        async def handler(payload: Any) -> None:
            await self._on_side(event, payload)
        return handler

    async def _on_side(self, event: str, payload: Any) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        key = (event,
               str(payload.get("account_id") or ""),
               str(payload.get("symbol") or ""))
        self.processed_count += 1
        if self._last_seen.get(key) == payload:
            self.duplicates_dropped += 1
            return
        self._last_seen[key] = dict(payload)
        await self._context.publish(EVENT_OUT, {
            "source_event": event,
            "validation_status": STATUS_SIDE_ONLY,
            "side_path_only": True,
            "payload": dict(payload),
        })

    async def _on_validated(self, envelope: Any) -> None:
        if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
            self.rejected_envelopes += 1
            return
        await self._on_side(str(envelope.get("source_event") or FALLBACK_SOURCE),
                            envelope["payload"])

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        state = HealthState.HEALTHY if self.processed_count else HealthState.DEGRADED
        return HealthStatus(
            state=state,
            message="side_processed=%d dropped=%d" % (
                self.processed_count, self.duplicates_dropped),
            details={"processed": self.processed_count,
                     "duplicates_dropped": self.duplicates_dropped,
                     "side_path_only": True})
