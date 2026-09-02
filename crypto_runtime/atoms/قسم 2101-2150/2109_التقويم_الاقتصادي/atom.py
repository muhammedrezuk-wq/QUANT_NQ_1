from __future__ import annotations

import math
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "3.1.0"

EVENT_TIME = "SYS_SECOND"
EVENT_UPCOMING = "market_data.calendar_event"
EVENT_WINDOW = "market_data.calendar_window"
EVENT_STATE = "market_data.calendar.state"

IMPACT_HIGH = "HIGH"
IMPACT_MEDIUM = "MEDIUM"
IMPACT_LOW = "LOW"
IMPACT_UNKNOWN = "UNKNOWN"
_ORDER = {IMPACT_LOW: 1, IMPACT_MEDIUM: 2, IMPACT_HIGH: 3}

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SOURCE = "UNAVAILABLE_NO_CALENDAR_SOURCE"
REASON_NO_TIME = "NO_TIME_INPUT"

BAD_SHAPE = "shape"
BAD_TIME = "bad_scheduled_at"
DUPLICATE = "duplicate"


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _impact(value: Any) -> str:
    if not isinstance(value, str):
        return IMPACT_UNKNOWN
    upper = value.strip().upper()
    if upper in (IMPACT_HIGH, "3", "H"):
        return IMPACT_HIGH
    if upper in (IMPACT_MEDIUM, "MED", "2", "M"):
        return IMPACT_MEDIUM
    if upper in (IMPACT_LOW, "1", "L"):
        return IMPACT_LOW
    return IMPACT_UNKNOWN


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._source_event = ""
        self._alert_before_s = 0.0
        self._keep_past_s = 0.0
        self._min_impact = ""
        self._events: dict[str, dict[str, Any]] = {}
        self._announced: set[str] = set()
        self._now_utc = 0.0
        self._in_window = False
        self._received = 0
        self._valid_received = 0
        self._published = 0
        self._last_state: tuple[bool, bool] | None = None
        self._rejected: dict[str, int] = {}
        self._restore_error = ""
        self._restored_pending_prune = False

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._source_event = str(cfg["source_event"])
        self._alert_before_s = float(cfg["alert_before_seconds"])
        self._keep_past_s = float(cfg["keep_past_seconds"])
        self._min_impact = str(cfg["min_impact"]).upper()
        context.subscribe(self._source_event, self._on_calendar)
        context.subscribe(EVENT_TIME, self._on_time)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _reject(self, reason: str) -> None:
        self._rejected[reason] = self._rejected.get(reason, 0) + 1

    def _passes_impact(self, impact: str) -> bool:
        if impact == IMPACT_UNKNOWN:
            return True
        return _ORDER.get(impact, 3) >= _ORDER.get(self._min_impact, 1)

    async def _on_calendar(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self._received += 1
        title = payload.get("title") or payload.get("event")
        scheduled_at = _to_float(payload.get("scheduled_at"))
        if not isinstance(title, str) or not title.strip():
            self._reject(BAD_SHAPE)
            return
        if scheduled_at is None or scheduled_at <= 0:
            self._reject(BAD_TIME)
            return
        impact = _impact(payload.get("impact_level"))
        if not self._passes_impact(impact):
            return
        key = str(payload.get("id") or "%s|%d" % (title.strip()[:80], int(scheduled_at)))
        if key in self._events:
            self._reject(DUPLICATE)
            return
        self._valid_received += 1
        self._events[key] = {
            "id": key, "title": title.strip(), "country": payload.get("country"),
            "currency": payload.get("currency"), "impact_level": impact,
            "scheduled_at": scheduled_at}

    async def _on_time(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = _to_float(payload.get("official_time"))
        if now is None:
            return
        self._now_utc = now
        cutoff = 0.0
        for key in [k for k, e in self._events.items()
                    if now - e["scheduled_at"] > cutoff]:
            self._events.pop(key, None)
            self._announced.discard(key)
        self._restored_pending_prune = False
        for key, event in sorted(self._events.items(),
                                 key=lambda kv: kv[1]["scheduled_at"]):
            remaining = event["scheduled_at"] - now
            if 0 <= remaining <= self._alert_before_s and key not in self._announced:
                self._announced.add(key)
                self._published += 1
                await self._context.publish(
                    EVENT_UPCOMING, {**event, "seconds_until": round(remaining, 1)})
        inside = any(0 <= e["scheduled_at"] - now <= self._alert_before_s
                     for e in self._events.values())
        known = self._valid_received > 0
        state = (known, inside)
        if state != self._last_state:
            self._last_state = state
            self._in_window = inside
            self._published += 1
            body = {"known": known, "in_event_window": inside,
                "status": "BLOCKED" if inside else "CLEAR" if known else "UNKNOWN",
                "allow_new_entries": known and not inside,
                "alert_before_seconds": self._alert_before_s, "utc": now}
            await self._context.publish(EVENT_WINDOW, dict(body))
            await self._context.publish(EVENT_STATE, dict(body))

    async def snapshot(self) -> dict[str, Any]:
        return {
            "version": ATOM_VERSION,
            "events": [dict(event) for event in self._events.values()],
            "announced": sorted(self._announced),
            "source_seen": self._received > 0,
            "valid_received": self._valid_received,
            "now_utc": self._now_utc,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or not isinstance(state.get("events"), list):
            self._events = {}
            self._announced = set()
            self._valid_received = 0
            self._restore_error = "CALENDAR_RESTORE_FAILED_UNKNOWN"
            raise ValueError(self._restore_error)
        restored: dict[str, dict[str, Any]] = {}
        for raw in state["events"]:
            if not isinstance(raw, dict):
                self._restore_error = "CALENDAR_RESTORE_FAILED_UNKNOWN"
                raise ValueError(self._restore_error)
            key = str(raw.get("id") or "").strip()
            title = str(raw.get("title") or "").strip()
            scheduled = _to_float(raw.get("scheduled_at"))
            if not key or not title or scheduled is None or scheduled <= 0:
                self._restore_error = "CALENDAR_RESTORE_FAILED_UNKNOWN"
                raise ValueError(self._restore_error)
            restored[key] = {**raw, "id": key, "title": title,
                             "scheduled_at": scheduled,
                             "impact_level": _impact(raw.get("impact_level"))}
        self._events = restored
        announced = state.get("announced")
        self._announced = ({str(x) for x in announced if str(x) in restored}
                           if isinstance(announced, list) else set())
        source_seen = state.get("source_seen") is True
        self._received = max(self._received, 1 if source_seen else 0)
        self._valid_received = max(len(restored), int(state.get("valid_received") or 0))
        self._now_utc = _to_float(state.get("now_utc")) or 0.0
        self._last_state = None
        self._restore_error = ""
        self._restored_pending_prune = True

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"received": self._received, "published": self._published,
                   "restore_error": self._restore_error,
                   "tracked": len(self._events), "in_window": self._in_window,
                   "rejected": {k: v for k, v in self._rejected.items() if v}}
        if not self._now_utc:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NO_TIME, details=details)
        if self._received == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NO_SOURCE, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="tracked=%d published=%d" % (len(self._events), self._published),
            details=details)
