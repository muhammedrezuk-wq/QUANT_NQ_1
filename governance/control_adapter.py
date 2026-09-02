"""Domain control adapters kept outside Core.

Core accepts a generic control-event publisher. This module owns the market
vocabulary and allowlists so the frozen Core does not know Crypto or Forex
business events.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable


_CONTROL_EVENTS: dict[str, frozenset[str]] = {
    "crypto": frozenset({
        "crypto.universe.override.command",
        "crypto.universe.scan.requested",
        # Owner-confirmed manual close result.  The governance endpoint
        # validates, audits, and deduplicates it before it reaches Core.
        "platform.trade_event",
    }),
    "forex": frozenset(),
}


ControlPublisher = Callable[[str, dict[str, Any]], Awaitable[None]]


def build_control_event_publisher(domain: str, event_bus: Any) -> ControlPublisher:
    """Return a fail-closed publisher for one externally selected domain."""
    allowed = _CONTROL_EVENTS.get(str(domain).strip().lower(), frozenset())

    async def publish(name: str, payload: dict[str, Any]) -> None:
        if name not in allowed:
            raise PermissionError("event not allowed for selected domain")
        await event_bus.publish(name, payload, publisher="governance.dashboard")

    return publish
