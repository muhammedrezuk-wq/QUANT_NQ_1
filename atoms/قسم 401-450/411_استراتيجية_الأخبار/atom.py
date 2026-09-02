from __future__ import annotations
from collections import deque
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import StrategyRuntime, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.1.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_NEWS = "market_data.news_received"
EVENT_ENRICHED = "market.news.enriched"
EVENT_OUT = "strategy.news.state"
EVENT_WINDOW = "news.trading_window.state"
COMPONENT_ID = "news_regime"
HIGH = "high"
MEDIUM = "medium"
LIGHT = "light"
UNKNOWN_FEED_CONFIDENCE = 50.0


def number(v):
    try:
        r = float(v)
    except (TypeError, ValueError):
        return None
    return r if r == r else None


def symbols(raw):
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [
        x.strip()
        for x in str(raw or "").split(",")
        if x.strip() and x.strip().upper() != "UNKNOWN"
    ]


@section_atom("400", "411")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._rt = StrategyRuntime(COMPONENT_ID, directional=False)
        self._by_symbol = {}
        self._seen_keys = deque(maxlen=1000)
        self._window_s = 3600
        self._before = 900
        self._after = 900
        self._seen = self._emitted = self._duplicates = 0
        self._no_time = 0
        self._no_tick_time = 0

    async def initialize(self, c):
        self._context = c
        self._rt.configure(c.config)
        self._window_s = int(c.config.get("recent_window_s", 3600))
        self._before = float(c.config.get("high_window_before_min", 15)) * 60
        self._after = float(c.config.get("high_window_after_min", 15)) * 60
        c.subscribe(EVENT_TICK, self._on_tick)
        c.subscribe(EVENT_NEWS, self._on_news)
        c.subscribe(EVENT_ENRICHED, self._on_news)

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    async def _on_news(self, p: dict[str, Any]):
        if not self._running or not isinstance(p, dict):
            return
        headline = str(
            p.get("headline_src") or p.get("headline_ar") or p.get("headline") or ""
        ).strip()
        if not headline:
            return
        key = " ".join(headline.lower().split())
        if key in self._seen_keys:
            self._duplicates += 1
            return
        self._seen_keys.append(key)
        impact = str(p.get("impact_level") or "").lower()
        grade = (
            HIGH
            if impact in ("high", "\u0639\u0627\u0644\u064a")
            else MEDIUM if impact == "medium" else LIGHT
        )
        # Owner stamp 2026-08-25: a headline with no publish time used to be
        # stamped with the wall clock, so a three-week-old item entered the
        # book looking like it broke this second -- and the high-impact window
        # is exactly a "did this happen within +/- 15 minutes" question.
        # Measured on the bridge table: 20 of 214 rows carry no publish time,
        # and the rows span 2026-08-04 to 2026-08-24. An invented time cannot
        # be told from a measured one, so the time stays UNKNOWN, is counted,
        # and the row is kept for the record but never claims freshness.
        stamp = number(p.get("published_at")) or number(p.get("timestamp"))
        if stamp is None:
            self._no_time += 1
        row = {
            "headline": headline,
            "grade": grade,
            "event_time": stamp,
            "source": p.get("source"),
        }
        for symbol in symbols(p.get("symbols")):
            self._by_symbol.setdefault(symbol, deque(maxlen=200)).appendleft(dict(row))

    async def _on_tick(self, p: dict[str, Any]):
        if not self._running or self._context is None or not isinstance(p, dict):
            return
        item = self._rt.ingest(p)
        if item is None:
            return
        tick, s = item
        self._seen += 1
        symbol = str(tick.get("symbol") or "")
        # Market time only -- never the wall clock. ingest() already refuses a
        # tick without a usable stamp, so the old "or time.time()" fallback was
        # unreachable; it is gone rather than left as a latent clock read.
        now = number(tick.get("source_timestamp")) or number(tick.get("timestamp"))
        if now is None:
            self._no_tick_time += 1
            return
        bucket = self._by_symbol.get(symbol, deque())
        timed = [row for row in bucket if row["event_time"] is not None]
        untimed = len(bucket) - len(timed)
        recent = [
            row
            for row in timed
            if now - row["event_time"] <= self._window_s
            or row["event_time"] - self._before
            <= now
            <= row["event_time"] + self._after
        ]
        high_active = [
            row
            for row in recent
            if row["grade"] == HIGH
            and row["event_time"] - self._before
            <= now
            <= row["event_time"] + self._after
        ]
        factor = 0.0 if high_active else 1.0
        confidence = 100.0 if recent or self._by_symbol else UNKNOWN_FEED_CONFIDENCE
        strength = clip(100.0 - factor * 100.0)
        if high_active:
            await self._context.publish(
                EVENT_WINDOW,
                {
                    "id": "news_regime_window",
                    "account_id": tick.get("account_id"),
                    "broker": tick.get("broker"),
                    "symbol": symbol,
                    "block": True,
                    "grade": HIGH,
                    "source_timestamp": high_active[0]["event_time"],
                },
            )
        card = self._rt.card(
            tick,
            s,
            direction=0,
            strength=strength,
            confidence=confidence,
            signal="high_impact_window" if high_active else "news_context_clear",
            context_factor=factor,
            evidence={
                "news_count": len(recent),
                "high_active": len(high_active),
                "context_factor": factor,
                # Declared, never hidden: headlines held without a usable time.
                "untimed_news": untimed,
            },
        )
        await self._context.publish(EVENT_OUT, card)
        self._emitted += 1

    async def snapshot(self):
        return {
            "runtime": self._rt.snapshot(),
            "by_symbol": {k: list(v) for k, v in self._by_symbol.items()},
            "seen_keys": list(self._seen_keys),
            "seen": self._seen,
            "emitted": self._emitted,
            "duplicates": self._duplicates,
            "no_time": self._no_time,
            "no_tick_time": self._no_tick_time,
        }

    async def restore(self, x):
        if not isinstance(x, dict):
            return
        self._rt.restore(x.get("runtime"))
        self._by_symbol = {
            str(k): deque(v, maxlen=200)
            for k, v in (x.get("by_symbol") or {}).items()
            if isinstance(v, list)
        }
        self._seen_keys = deque((str(v) for v in x.get("seen_keys", [])), maxlen=1000)
        self._seen = int(x.get("seen", 0))
        self._emitted = int(x.get("emitted", 0))
        self._duplicates = int(x.get("duplicates", 0))
        self._no_time = int(x.get("no_time", 0))
        self._no_tick_time = int(x.get("no_tick_time", 0))

    async def health_check(self):
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY if self._seen else HealthState.DEGRADED,
            message="ticks=%d news=%d no_time=%d"
            % (
                self._seen,
                sum(len(v) for v in self._by_symbol.values()),
                self._no_time,
            ),
            details={
                "ticks": self._seen,
                "emitted": self._emitted,
                "duplicates": self._duplicates,
                "no_time": self._no_time,
                "no_tick_time": self._no_tick_time,
            },
        )
