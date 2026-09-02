from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN = "crypto.market.ticker.all"
EVENT_TICK = "crypto.feed.tick"
EVENT_STATE = "crypto.feed.state"


class Atom(AtomBase):
    """Phase A feed fan-out: publish every selected ticker key, no senses."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._membership: dict[str, str] = {}
        self._universe_version: str | None = None
        self._last_feed_at: float | None = None
        self._published = 0
        self._batches = 0
        self._last_error = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_ticker_batch)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _set_membership(self, payload: dict[str, Any]) -> None:
        membership = payload.get("membership") if isinstance(payload, dict) else None
        if not isinstance(membership, dict):
            return
        self._universe_version = str(payload.get("universe_version") or membership.get("universe_version") or "") or None
        selected: dict[str, str] = {}
        for row in membership.get("core") or []:
            if isinstance(row, dict) and row.get("symbol"):
                selected[str(row["symbol"]).upper()] = "core"
        for row in membership.get("outer") or []:
            if isinstance(row, dict) and row.get("symbol"):
                selected[str(row["symbol"]).upper()] = "outer"
        self._membership = selected

    async def _on_ticker_batch(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._set_membership(payload)
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return
        version = str(payload.get("universe_version") or self._universe_version or "") or None
        now = time.time()
        batch_published = 0
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("symbol") or "").upper()
            ring = self._membership.get(symbol)
            if ring is None:
                continue
            # Explicitly retain the full normalized ticker vocabulary. The feed
            # is the contract boundary; later senses must not re-fetch MEXC.
            tick = {
                "event_type": "crypto.feed.tick",
                "provider": raw.get("provider", "MEXC"),
                "market": raw.get("market", "futures"),
                "market_segment": raw.get("market_segment", "futures_usdt"),
                "contract_type": raw.get("contract_type"),
                "symbol": symbol,
                "ring": ring,
                "universe_version": version,
                "last_price": raw.get("last_price"),
                "bid": raw.get("bid"),
                "ask": raw.get("ask"),
                "volume24_contracts": raw.get("volume24_contracts"),
                "amount24_usd": raw.get("amount24_usd"),
                "high24": raw.get("high24"),
                "low24": raw.get("low24"),
                "rise_fall_rate": raw.get("rise_fall_rate"),
                "rise_fall_value": raw.get("rise_fall_value"),
                "open_interest": raw.get("open_interest"),
                "index_price": raw.get("index_price"),
                "fair_price": raw.get("fair_price"),
                "funding_rate": raw.get("funding_rate"),
                "price_tick_size": raw.get("price_tick_size"),
                "contract_size": raw.get("contract_size"),
                "base_asset": raw.get("base_asset"),
                "quote_asset": raw.get("quote_asset"),
                "settle_asset": raw.get("settle_asset"),
                "asset_class": raw.get("asset_class"),
                "spread_ticks": raw.get("spread_ticks"),
                "daily_range_pct": raw.get("daily_range_pct"),
                "source_timestamp_ms": raw.get("timestamp_ms"),
                "received_at": now,
            }
            await self._context.publish(EVENT_TICK, tick)
            batch_published += 1
        self._published += batch_published
        self._batches += 1
        self._last_feed_at = now
        await self._context.publish(EVENT_STATE, {
            "status": "ACTIVE" if self._membership else "NO_MEMBERSHIP",
            "universe_version": version,
            "core_count": sum(1 for ring in self._membership.values() if ring == "core"),
            "outer_count": sum(1 for ring in self._membership.values() if ring == "outer"),
            "batch_count": self._batches,
            "published_ticks": self._published,
            "last_feed_at": now,
        })

    async def health_check(self) -> HealthStatus:
        details = {
            "membership": len(self._membership),
            "universe_version": self._universe_version,
            "batches": self._batches,
            "published_ticks": self._published,
            "last_feed_at": self._last_feed_at,
            "last_error": self._last_error,
        }
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_feed_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_UNIVERSE_FEED", details=details)
        return HealthStatus(state=HealthState.HEALTHY, message=f"symbols={len(self._membership)}", details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {
            "version": ATOM_VERSION,
            "membership": dict(self._membership),
            "universe_version": self._universe_version,
            "published": self._published,
            "batches": self._batches,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._membership = {str(k): str(v) for k, v in (state.get("membership") or {}).items()}
        self._universe_version = state.get("universe_version")
        self._published = int(state.get("published", 0))
        self._batches = int(state.get("batches", 0))
