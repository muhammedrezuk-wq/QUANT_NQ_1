from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

from shared.unified_contract import (
    STATE_DORMANT, STATE_READY, STATE_ANALYZING,
    dir_norm, pct, stamp_unified,
)

ATOM_VERSION = "2.0.1"

EVENT_OUT = "market_data.trade_tape_updated"

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
SIDE_UNKNOWN = "UNKNOWN"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SOURCE = "UNAVAILABLE_NO_TRADE_SOURCE"
REASON_DORMANT = "DORMANT_NO_PUBLISHER_FOR_SOURCE_EVENT"

BAD_SHAPE = "shape"
BAD_PRICE = "bad_price"

REQUIRED_DEPTH_DEFAULT = 30.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _side(value: Any) -> str:
    if not isinstance(value, str):
        return SIDE_UNKNOWN
    upper = value.strip().upper()
    if upper in (SIDE_BUY, "B", "BID"):
        return SIDE_BUY
    if upper in (SIDE_SELL, "S", "ASK"):
        return SIDE_SELL
    return SIDE_UNKNOWN


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._source_event = ""
        self._window_size = 0
        self._publish_every = 0
        self._required_depth = REQUIRED_DEPTH_DEFAULT
        self._dormant_declared = False
        self._tape: dict[str, deque] = {}
        self._since_publish: dict[str, int] = {}
        self._received = 0
        self._published = 0
        self._rejected: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._source_event = str(cfg["source_event"])
        self._window_size = int(cfg["window_size"])
        self._publish_every = int(cfg["publish_every_n_trades"])
        self._required_depth = max(0.0, min(100.0,
            _to_float(cfg.get("required_depth", REQUIRED_DEPTH_DEFAULT)) or REQUIRED_DEPTH_DEFAULT))
        context.subscribe(self._source_event, self._on_trade)

    async def start(self) -> None:
        self._running = True
        if self._context is not None and not self._dormant_declared:
            self._dormant_declared = True
            self._published += 1
            await self._context.publish(EVENT_OUT, self._build_dormant_state())

    def _build_dormant_state(self) -> dict[str, Any]:
        return stamp_unified(
            {
                "declared": True,
                "reason": REASON_DORMANT,
                "trades_in_window": 0,
                "buy_volume": 0.0, "sell_volume": 0.0,
                "unknown_volume": 0.0, "total_volume": 0.0,
            },
            section_id="market_data",
            atom_id=self._context.atom_id if self._context else None,
            state=STATE_DORMANT,
            direction=0.0, strength=0.0, confidence=0.0,
            weight=0.0, ratio=0.0,
            current_depth=0.0, required_depth=self._required_depth,
            sequence=self._received,
        )

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _reject(self, reason: str) -> None:
        self._rejected[reason] = self._rejected.get(reason, 0) + 1

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._received += 1
        symbol = str(payload.get("symbol") or "")
        price = _to_float(payload.get("price"))
        size = _to_float(payload.get("size", payload.get("volume")))
        if not symbol or price is None or size is None:
            self._reject(BAD_SHAPE)
            return
        if price <= 0 or size < 0:
            self._reject(BAD_PRICE)
            return
        tape = self._tape.setdefault(symbol, deque(maxlen=self._window_size))
        tape.append({"price": price, "size": size, "side": _side(payload.get("side")),
                     "timestamp": payload.get("timestamp")})
        self._since_publish[symbol] = self._since_publish.get(symbol, 0) + 1
        if self._since_publish[symbol] >= self._publish_every:
            self._since_publish[symbol] = 0
            self._published += 1
            await self._context.publish(EVENT_OUT, self._build_state(symbol, payload))

    def _build_state(self, symbol: str, payload: dict[str, Any] | None = None
                      ) -> dict[str, Any]:
        trades = list(self._tape.get(symbol) or deque())
        buy_volume = sum(t["size"] for t in trades if t["side"] == SIDE_BUY)
        sell_volume = sum(t["size"] for t in trades if t["side"] == SIDE_SELL)
        unknown_volume = sum(t["size"] for t in trades if t["side"] == SIDE_UNKNOWN)
        known = buy_volume + sell_volume
        current_depth = pct(round((len(trades) / self._window_size) * 100.0, 4)
                             if self._window_size else 0.0)
        analysis_state = STATE_READY if current_depth >= self._required_depth \
                                       else STATE_ANALYZING
        src = payload or {}
        return stamp_unified(
            {
                "symbol": symbol,
                "trades_in_window": len(trades),
                "last_price": trades[-1]["price"] if trades else None,
                "last_size": trades[-1]["size"] if trades else None,
                "total_volume": buy_volume + sell_volume + unknown_volume,
                "buy_volume": buy_volume, "sell_volume": sell_volume,
                "unknown_volume": unknown_volume,
                "buy_ratio": round(buy_volume / known, 6) if known else None,
                "recent": trades[-10:],
                "source_timestamp": src.get("timestamp"),
                "direction": 0.0,
                "strength": 0.0,
                "weight": 0.0, "ratio": 0.0,
            },
            section_id="market_data",
            atom_id=self._context.atom_id if self._context else None,
            state=analysis_state,
            confidence=current_depth,
            current_depth=current_depth,
            required_depth=self._required_depth,
            sequence=self._received,
        )

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {
            "received": self._received, "published": self._published,
            "rejected": {k: v for k, v in self._rejected.items() if v},
            "symbols": len(self._tape),
            "source_event": self._source_event,
            "state": STATE_DORMANT if self._received == 0 else STATE_READY
                        if all(self._since_publish.get(s, 0) >= self._publish_every
                               for s in self._tape) else STATE_ANALYZING,
        }
        if self._received == 0:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_SOURCE, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d published=%d" % (
                                len(self._tape), self._published),
                            details=details)
