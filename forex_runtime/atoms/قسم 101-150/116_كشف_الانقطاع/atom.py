from __future__ import annotations

import math
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "3.1.0"

EVENT_TIME = "SYS_SECOND"
EVENT_INTERRUPTED = "market_data.feed_interrupted"
EVENT_RECOVERED = "market_data.feed_recovered"
EVENT_STATE = "market_data.feed.state"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TIME = "NO_TIME_INPUT"

STATE_ACTIVE = "ACTIVE"
STATE_STALE = "STALE"
STATE_DEAD = "DEAD"
STATE_NEVER_SEEN = "NEVER_SEEN"
_SILENT_STATES = {STATE_STALE, STATE_DEAD}

_FEED_EVENTS = ("feed.mt5.tick", "feed.ctrader.tick")
_DEAD_FACTOR = 2.0


def num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._stale_after = 0.0
        self._dead_after = 0.0
        self._now = 0.0
        self._last_activity: float | None = None
        self._seen = False
        self._state: str | None = None
        self._interruption_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._stale_after = float(context.config["max_silence_seconds"])
        self._dead_after = float(context.config.get(
            "dead_after_seconds", self._stale_after * _DEAD_FACTOR))
        for event in _FEED_EVENTS:
            context.subscribe(event, self._on_feed)
        context.subscribe(EVENT_TIME, self._on_time)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _publish_state(self, state: str, elapsed: float | None = None) -> None:
        if self._context is None or state == self._state:
            return
        previous = self._state
        self._state = state
        body = {
            "status": state,
            "seen": self._seen,
            "stale_after_seconds": self._stale_after,
            "dead_after_seconds": self._dead_after,
        }
        if elapsed is not None:
            body["elapsed_seconds"] = round(elapsed, 1)
        if self._now > 0:
            body["timestamp"] = self._now
        await self._context.publish(EVENT_STATE, body)
        if state in _SILENT_STATES and previous not in _SILENT_STATES:
            self._interruption_count += 1
            await self._context.publish(EVENT_INTERRUPTED, {
                **body, "interruption_count": self._interruption_count})
        elif state == STATE_ACTIVE and previous in _SILENT_STATES:
            await self._context.publish(EVENT_RECOVERED, {
                **body, "interruption_count": self._interruption_count})

    async def _on_feed(self, payload: Any) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self._seen = True
        if self._now > 0:
            self._last_activity = self._now
            await self._publish_state(STATE_ACTIVE, 0.0)

    async def _on_time(self, payload: Any) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = num(payload.get("official_time"))
        if now is None:
            return
        self._now = now
        if not self._seen:
            await self._publish_state(STATE_NEVER_SEEN)
            return
        if self._last_activity is None:
            self._last_activity = now
            await self._publish_state(STATE_ACTIVE, 0.0)
            return
        elapsed = max(0.0, now - self._last_activity)
        if elapsed > self._dead_after:
            state = STATE_DEAD
        elif elapsed > self._stale_after:
            state = STATE_STALE
        else:
            state = STATE_ACTIVE
        await self._publish_state(state, elapsed)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY,
                                message=REASON_NOT_STARTED)
        details = {
            "status": self._state or STATE_NEVER_SEEN,
            "seen": self._seen,
            "last_activity": self._last_activity,
            "interruption_count": self._interruption_count,
        }
        if self._state in (None, STATE_NEVER_SEEN):
            return HealthStatus(state=HealthState.DEGRADED,
                                message=STATE_NEVER_SEEN, details=details)
        if self._state in _SILENT_STATES:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=self._state, details=details)
        return HealthStatus(state=HealthState.HEALTHY, message=STATE_ACTIVE,
                            details=details)
