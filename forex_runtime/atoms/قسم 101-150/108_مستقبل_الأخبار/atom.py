from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.1"
# v2.0.1 (2026-08-27, item 21/27 of the 27-atom review -- verification
# only, no code change): 615 and 616 both publish market.news by
# deliberate design (parallel sources, not a replacement). Their raw
# bridge-table row ids could collide across the two independent source
# databases, and this atom's dedup key (self._seen_keys) is exactly that
# "id" field -- an end-to-end test proved the collision drops a real,
# distinct headline as a false duplicate on the pre-fix producers, and
# that 615/616's own fix (namespacing "id" per atom) resolves it from
# this atom's side without any change needed here.

EVENT_OUT = "market_data.news_received"

IMPACT_HIGH = "HIGH"
IMPACT_MEDIUM = "MEDIUM"
IMPACT_LOW = "LOW"
IMPACT_UNKNOWN = "UNKNOWN"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SOURCE = "UNAVAILABLE_NO_NEWS_SOURCE"

BAD_SHAPE = "shape"
DUPLICATE = "duplicate"


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


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


def _sentiment(value: Any) -> float | None:
    score = _to_float(value)
    if score is None or not -1.0 <= score <= 1.0:
        return None
    return round(score, 4)


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._source_event = ""
        self._recent_size = 0
        self._recent: deque = deque()
        self._seen_keys: deque = deque()
        self._received = 0
        self._published = 0
        self._rejected: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._source_event = str(cfg["source_event"])
        self._recent_size = int(cfg["recent_size"])
        self._recent = deque(maxlen=self._recent_size)
        self._seen_keys = deque(maxlen=self._recent_size * 4)
        context.subscribe(self._source_event, self._on_news)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _reject(self, reason: str) -> None:
        self._rejected[reason] = self._rejected.get(reason, 0) + 1

    async def _on_news(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._received += 1
        headline = payload.get("headline") or payload.get("title")
        if not isinstance(headline, str) or not headline.strip():
            self._reject(BAD_SHAPE)
            return
        headline = headline.strip()
        key = payload.get("id") or "%s|%s" % (headline[:120], payload.get("published_at"))
        if key in self._seen_keys:
            self._reject(DUPLICATE)
            return
        self._seen_keys.append(key)
        item: dict[str, Any] = {
            "headline": headline,
            "source": payload.get("source"),
            "symbols": payload.get("symbols") or [],
            "sentiment_score": _sentiment(payload.get("sentiment_score")),
            "impact_level": _impact(payload.get("impact_level")),
            "published_at": payload.get("published_at"),
        }
        ts = payload.get("timestamp")
        if isinstance(ts, (int, float)):
            item["timestamp"] = ts
        self._recent.appendleft(item)
        self._published += 1
        await self._context.publish(EVENT_OUT, item)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"received": self._received, "published": self._published,
                   "rejected": {k: v for k, v in self._rejected.items() if v},
                   "recent": len(self._recent)}
        if self._received == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NO_SOURCE, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="published=%d" % self._published, details=details)
