from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.0.0"

EVENT_IN = "market_data.depth_updated"
EVENT_OUT = "liquidity.microprice.state"

STATE_READY = "READY"
STATE_NOT_READY = "NOT_READY"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SNAPSHOTS = "NO_DEPTH_SNAPSHOTS_YET"
BAD_SHAPE = "shape"

# الميكرو-سعر (Gatheral/Stoikov) — السعر العادل الموزون بحجمَي أفضل مستوى:
#   micro = (P_bid·Q_ask + P_ask·Q_bid) / (Q_bid + Q_ask)
# حين يثقل جانب الطلب (Q_bid كبير) يميل العادل نحو العرض ⇒ ضغط شراء.
# ⛔ من الدفتر وحده — لا صفقة ولا حجم منفَّذ (622 عقود فروقات بلا شريط صفقات،
#    §٥٤ القسم 250). كل رقمٍ على البطاقة من لقطة العمق نفسها، لا مخترع.


def _num(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


def _level(row: Any) -> tuple[float, float] | None:
    """(سعر، حجم) من صفّ دفتر [price, size] — None إن كان مشوَّهًا."""
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return None
    price, size = _num(row[0]), _num(row[1])
    if price <= 0 or size < 0:
        return None
    return price, size


@section_atom("250", "262")
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
        bids = payload.get("bids")
        asks = payload.get("asks")
        best_bid = _level(bids[0]) if isinstance(bids, list) and bids else None
        best_ask = _level(asks[0]) if isinstance(asks, list) and asks else None
        if not symbol or best_bid is None or best_ask is None:
            self._rejected += 1
            return
        p_bid, q_bid = best_bid
        p_ask, q_ask = best_ask
        queue = q_bid + q_ask
        if p_ask <= p_bid or queue <= 0.0:            # دفتر متقاطع أو بلا أحجام
            self._rejected += 1
            return
        micro = (p_bid * q_ask + p_ask * q_bid) / queue
        mid = (p_bid + p_ask) / 2.0
        half_spread = (p_ask - p_bid) / 2.0
        # موضع العادل داخل السبريد ‎[-100..100]‎: نحو العرض = ضغط شراء موجب.
        direction = max(-100.0, min(100.0, (micro - mid) / half_spread * 100.0))
        tilt = micro - mid
        tilt_bps = (tilt / mid * 1e4) if mid > 0 else 0.0
        levels = _num(payload.get("levels")) or float(min(len(bids), len(asks)))
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
            "id": "microprice",
            "timeframe": str(payload.get("timeframe") or ""),
            "status": "ok",
            "signal": ("buy" if direction > 0 else "sell" if direction < 0 else "neutral"),
            "direction": round(direction, 4),
            "strength": round(abs(direction), 4),
            "confidence": round(evidence, 4),
            "current_depth": round(evidence, 4),
            "required_depth": round(self._required_levels * 10.0, 4),
            "weight": self._weight,
            "weight_applied": self._weight if ready else 0.0,
            "ready": ready,
            "analysis_state": state,
            "state": state,
            "metadata": {
                "method": "microprice_stoikov",
                "microprice": round(micro, 10),
                "mid": round(mid, 10),
                "tilt": round(tilt, 10),
                "tilt_bps": round(tilt_bps, 4),
                "best_bid": p_bid, "best_ask": p_ask,
                "best_bid_size": q_bid, "best_ask_size": q_ask,
                "spread": round(p_ask - p_bid, 10),
                "levels": int(levels),
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
