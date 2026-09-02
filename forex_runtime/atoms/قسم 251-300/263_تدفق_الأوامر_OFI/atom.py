from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.0.0"

EVENT_IN = "market_data.depth_updated"
EVENT_OUT = "liquidity.ofi.state"

STATE_READY = "READY"
STATE_NOT_READY = "NOT_READY"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SNAPSHOTS = "NO_DEPTH_SNAPSHOTS_YET"

# اختلال تدفّق الأوامر OFI (Cont/Kukanov/Stoikov) — صافي التغيّر في طابورَي
# أفضل عرض/طلب بين لقطتين متتاليتين:
#   جانب الطلب: سعرٌ صعد ⇒ +حجمه · ثبت ⇒ +فرق الحجم · هبط ⇒ −الحجم السابق
#   جانب العرض: سعرٌ هبط ⇒ +حجمه · ثبت ⇒ +فرق الحجم · صعد ⇒ −الحجم السابق
#   OFI = تدفّق الطلب − تدفّق العرض   (موجب = ضغط شراء صافٍ)
# ⛔ من الدفتر وحده (لقطتان)، لا صفقة. أوّل لقطةٍ لا سابق لها ⇒ إحماء صادق
#    بلا اتجاه (UNKNOWN ≠ NEUTRAL، §القسم 250).


def _num(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


def _best(rows: Any) -> tuple[float, float] | None:
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return None
    price, size = _num(row[0]), _num(row[1])
    if price <= 0 or size < 0:
        return None
    return price, size


@section_atom("250", "263")
class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._weight = 20.0
        self._required_levels = 3.0
        self._prev: dict[str, tuple[float, float, float, float]] = {}
        self._received = 0
        self._published = 0
        self._rejected = 0
        self._warming = 0

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
        best_bid = _best(payload.get("bids"))
        best_ask = _best(payload.get("asks"))
        if not symbol or best_bid is None or best_ask is None:
            self._rejected += 1
            return
        p_bid, q_bid = best_bid
        p_ask, q_ask = best_ask
        if p_ask <= p_bid:
            self._rejected += 1
            return
        levels = _num(payload.get("levels")) or 1.0
        evidence = min(100.0, levels * 10.0)
        prev = self._prev.get(symbol)
        self._prev[symbol] = (p_bid, q_bid, p_ask, q_ask)
        head = {
            "symbol": symbol, "account_id": payload.get("account_id"),
            "broker": payload.get("broker"), "provider": payload.get("provider"),
            "id": "ofi", "timeframe": str(payload.get("timeframe") or ""),
            "required_depth": round(self._required_levels * 10.0, 4),
            "weight": self._weight,
        }
        if prev is None:
            # إحماء: لا سابق ⇒ لا اتجاه (نُغفِل حقل direction فيُعلَن مجهولًا).
            self._warming += 1
            self._published += 1
            await self._context.publish(EVENT_OUT, {
                **head, "status": "insufficient_data",
                "confidence": 0.0, "current_depth": round(evidence, 4),
                "weight_applied": 0.0, "ready": False,
                "analysis_state": STATE_NOT_READY, "state": STATE_NOT_READY,
                "metadata": {"method": "ofi_cont_kukanov",
                             "reason": "awaiting_prior_snapshot"},
            })
            return
        pp_bid, pq_bid, pp_ask, pq_ask = prev
        e_bid = q_bid if p_bid > pp_bid else (q_bid - pq_bid) if p_bid == pp_bid else -pq_bid
        e_ask = q_ask if p_ask < pp_ask else (q_ask - pq_ask) if p_ask == pp_ask else -pq_ask
        ofi = e_bid - e_ask
        scale = q_bid + q_ask
        direction = max(-100.0, min(100.0, ofi / scale * 100.0)) if scale > 0 else 0.0
        ready = levels >= self._required_levels
        state = STATE_READY if ready else STATE_NOT_READY
        self._published += 1
        await self._context.publish(EVENT_OUT, {
            **head, "status": "ok",
            "signal": ("buy" if direction > 0 else "sell" if direction < 0 else "neutral"),
            "direction": round(direction, 4),
            "strength": round(abs(direction), 4),
            "confidence": round(evidence, 4),
            "current_depth": round(evidence, 4),
            "weight_applied": self._weight if ready else 0.0,
            "ready": ready, "analysis_state": state, "state": state,
            "metadata": {
                "method": "ofi_cont_kukanov",
                "ofi": round(ofi, 6), "e_bid": round(e_bid, 6), "e_ask": round(e_ask, 6),
                "best_bid": p_bid, "best_ask": p_ask,
                "best_bid_size": q_bid, "best_ask_size": q_ask,
                "levels": int(levels),
            },
        })

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"received": self._received, "published": self._published,
                   "rejected": self._rejected, "warming": self._warming,
                   "tracked": len(self._prev)}
        if self._received == 0:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_SNAPSHOTS, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="published=%d rejected=%d" % (self._published, self._rejected),
            details=details)
