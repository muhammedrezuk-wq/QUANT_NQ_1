from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.0.0"

EVENT_IN = "market_data.depth_updated"
EVENT_OUT = "liquidity.depth.state"

STATE_READY = "READY"
STATE_NOT_READY = "NOT_READY"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SNAPSHOTS = "NO_DEPTH_SNAPSHOTS_YET"
BAD_SHAPE = "shape"

# Campaign 1-449, batch A (owner order 2026-08-23): the depth analyzer the
# audit missed (A 90-35: "depth arrives and no atom analyzes it" -- 106
# receives and publishes, 101 aggregates, 113 cleans, nobody analyzes).
# One snapshot in, one honest card out: imbalance is the measured book
# pressure -- every number on the card comes from the snapshot, none invented.


def _num(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


@section_atom("250", "261")
class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._weight = 20.0
        self._required_levels = 3.0
        self._received = 0
        self._published = 0
        self._rejected = 0
        self._snapshots = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._weight = _num(cfg.get("weight", 20.0))
        self._required_levels = max(1.0, _num(cfg.get("required_levels", 3.0)))
        context.subscribe(EVENT_IN, self._on_depth)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_depth(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._received += 1
        symbol = str(payload.get("symbol") or "").strip()
        imbalance = _num(payload.get("imbalance"))
        levels = _num(payload.get("levels"))
        if not symbol or levels <= 0:
            self._rejected += 1
            return
        imbalance_pct = max(-100.0, min(100.0, imbalance * 100.0))
        evidence = min(100.0, levels * 10.0)
        ready = levels >= self._required_levels
        state = STATE_READY if ready else STATE_NOT_READY
        self._snapshots += 1
        self._published += 1
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol,
            "account_id": payload.get("account_id"),
            "broker": payload.get("broker"),
            "provider": payload.get("provider"),
            "id": "depth",
            "timeframe": str(payload.get("timeframe") or ""),
            "status": "ok",
            "signal": ("buy" if imbalance_pct > 0
                       else "sell" if imbalance_pct < 0 else "neutral"),
            "direction": round(imbalance_pct, 4),
            "strength": round(abs(imbalance_pct), 4),
            "confidence": round(evidence, 4),
            "current_depth": round(evidence, 4),
            "required_depth": round(self._required_levels * 10.0, 4),
            "weight": self._weight,
            "weight_applied": self._weight if ready else 0.0,
            "ready": ready,
            "analysis_state": state,
            "state": state,
            "metadata": {
                "method": "book_imbalance",
                "levels": int(levels),
                "bid_volume": payload.get("bid_volume"),
                "ask_volume": payload.get("ask_volume"),
                "mid": payload.get("mid"),
                "spread": payload.get("spread"),
                "best_bid": payload.get("best_bid"),
                "best_ask": payload.get("best_ask"),
                "imbalance_raw": payload.get("imbalance"),
            },
        })

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"received": self._received, "published": self._published,
                   "rejected": self._rejected, "snapshots": self._snapshots,
                   "required_levels": self._required_levels}
        if self._received == 0:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_SNAPSHOTS, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="published=%d rejected=%d" % (self._published, self._rejected),
            details=details)
