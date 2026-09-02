from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.0"

EVENT_OUT = "market_data.depth_updated"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SOURCE = "UNAVAILABLE_NO_DEPTH_SOURCE"

BAD_SHAPE = "shape"
BAD_CROSSED = "crossed_book"
BAD_ORDER = "unsorted"
BAD_EMPTY = "empty_side"


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._source_event = ""
        self._max_levels = 0
        self._received = 0
        self._published = 0
        self._rejected: dict[str, int] = {}
        self._symbols: set[str] = set()

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._source_event = str(cfg["source_event"])
        self._max_levels = int(cfg["max_levels"])
        context.subscribe(self._source_event, self._on_depth)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _reject(self, reason: str) -> None:
        self._rejected[reason] = self._rejected.get(reason, 0) + 1

    def _levels(self, raw: Any) -> list[tuple[float, float]] | None:
        if not isinstance(raw, list):
            return None
        out: list[tuple[float, float]] = []
        for item in raw[:self._max_levels]:
            if isinstance(item, dict):
                price = _to_float(item.get("price"))
                size = _to_float(item.get("size", item.get("volume")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                price, size = _to_float(item[0]), _to_float(item[1])
            else:
                return None
            if price is None or size is None or price <= 0 or size < 0:
                return None
            out.append((price, size))
        return out

    async def _on_depth(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._received += 1
        symbol = str(payload.get("symbol") or "")
        bids = self._levels(payload.get("bids"))
        asks = self._levels(payload.get("asks"))
        if not symbol or bids is None or asks is None:
            self._reject(BAD_SHAPE)
            return
        if not bids or not asks:
            self._reject(BAD_EMPTY)
            return
        if any(bids[i][0] < bids[i + 1][0] for i in range(len(bids) - 1)):
            self._reject(BAD_ORDER)
            return
        if any(asks[i][0] > asks[i + 1][0] for i in range(len(asks) - 1)):
            self._reject(BAD_ORDER)
            return
        best_bid, best_ask = bids[0][0], asks[0][0]
        if best_bid >= best_ask:
            self._reject(BAD_CROSSED)
            return
        bid_volume = sum(size for _, size in bids)
        ask_volume = sum(size for _, size in asks)
        total = bid_volume + ask_volume
        state: dict[str, Any] = {
            "symbol": symbol,
            "bids": [[p, s] for p, s in bids],
            "asks": [[p, s] for p, s in asks],
            "levels": min(len(bids), len(asks)),
            "best_bid": best_bid, "best_ask": best_ask,
            "spread": round(best_ask - best_bid, 10),
            "mid": round((best_bid + best_ask) / 2.0, 10),
            "bid_volume": bid_volume, "ask_volume": ask_volume,
            "imbalance": round((bid_volume - ask_volume) / total, 6) if total else 0.0,
        }
        provider = payload.get("provider")
        if provider is not None:
            state["provider"] = provider
        ts = payload.get("timestamp")
        if isinstance(ts, (int, float)):
            state["timestamp"] = ts
        self._symbols.add(symbol)
        self._published += 1
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"received": self._received, "published": self._published,
                   "rejected": {k: v for k, v in self._rejected.items() if v},
                   "symbols": len(self._symbols)}
        if self._received == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NO_SOURCE, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="symbols=%d published=%d" % (len(self._symbols), self._published),
            details=details)
