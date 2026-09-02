from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.0.0"

EVENT_IN = "market_data.depth_updated"
EVENT_OUT = "liquidity.price_impact.state"

STATE_READY = "READY"
STATE_NOT_READY = "NOT_READY"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SNAPSHOTS = "NO_DEPTH_SNAPSHOTS_YET"

# أثر السعر بالدفتر (كايل λ / أميهود من شكل الدفتر) — تكلفة السير في الدفتر
# لتنفيذ كمّيّة `impact_qty`: متوسّط السعر المُحقَّق مقابل المنتصف بالنقاط الأساس.
#   impact_up   = تكلفة الشراء (سير في العروض)   ⇒ رقّة جانب العرض
#   impact_down = تكلفة البيع   (سير في الطلبات)  ⇒ رقّة جانب الطلب
#   الهشاشة = متوسّطهما (أعلى = دفترٌ أرقّ) ·  الاتجاه = عدم التماثل:
#   الجهة الأعلى انزلاقًا (الأرقّ، الأقلّ مقاومةً) يميل السعر نحوها —
#   عرضٌ رقيق (impact_up عالٍ) ⇒ صعوديّ · طلبٌ رقيق (impact_down عالٍ) ⇒ هبوطيّ
#   (يوافق منطق اختلال العمق 261: عرضٌ ضعيف = مقاومة أقلّ فوق).
# ⛔ من الدفتر وحده — لا صفقة (622 عقود فروقات بلا شريط، §القسم 250).
#    عمقٌ لا يكفي لتنفيذ الكمّيّة ⇒ insufficient_data صادق لا رقمٌ مبتور.


def _num(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


def _walk(rows: Any, qty: float) -> tuple[float, bool] | None:
    """(متوسّط سعر التنفيذ، هل امتلأت الكمّيّة) بالسير في مستويات الدفتر."""
    if not isinstance(rows, list) or not rows or qty <= 0:
        return None
    remaining, cost, filled = qty, 0.0, 0.0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price, size = _num(row[0]), _num(row[1])
        if price <= 0 or size <= 0:
            continue
        take = size if size < remaining else remaining
        cost += take * price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if filled <= 0:
        return None
    return cost / filled, remaining <= 1e-12


@section_atom("250", "264")
class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._weight = 20.0
        self._required_levels = 3.0
        self._impact_qty = 50.0
        self._received = 0
        self._published = 0
        self._rejected = 0
        self._snapshots = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._weight = _num(cfg.get("weight", 20.0))
        self._required_levels = max(1.0, _num(cfg.get("required_levels", 3.0)))
        self._impact_qty = max(1e-9, _num(cfg.get("impact_qty", 50.0)))
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
        if (not symbol or not isinstance(bids, list) or not bids
                or not isinstance(asks, list) or not asks):
            self._rejected += 1
            return
        best_bid = _num(bids[0][0]) if isinstance(bids[0], (list, tuple)) else 0.0
        best_ask = _num(asks[0][0]) if isinstance(asks[0], (list, tuple)) else 0.0
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            self._rejected += 1
            return
        mid = (best_bid + best_ask) / 2.0
        up = _walk(asks, self._impact_qty)          # تكلفة الشراء
        down = _walk(bids, self._impact_qty)         # تكلفة البيع
        if up is None or down is None:
            self._rejected += 1
            return
        avg_up, full_up = up
        avg_down, full_down = down
        impact_up = (avg_up - mid) / mid * 1e4       # نقاط أساس ≥ 0
        impact_down = (mid - avg_down) / mid * 1e4
        fragility = (impact_up + impact_down) / 2.0
        lambda_up = impact_up / self._impact_qty
        lambda_down = impact_down / self._impact_qty
        levels = _num(payload.get("levels")) or float(min(len(bids), len(asks)))
        evidence = min(100.0, levels * 10.0)
        self._snapshots += 1
        self._published += 1
        head = {
            "symbol": symbol, "account_id": payload.get("account_id"),
            "broker": payload.get("broker"), "provider": payload.get("provider"),
            "id": "price_impact", "timeframe": str(payload.get("timeframe") or ""),
            "current_depth": round(evidence, 4),
            "required_depth": round(self._required_levels * 10.0, 4),
            "weight": self._weight,
        }
        meta = {
            "method": "book_impact_kyle", "impact_qty": self._impact_qty,
            "impact_up_bps": round(impact_up, 4), "impact_down_bps": round(impact_down, 4),
            "fragility_bps": round(fragility, 4),
            "lambda_up": round(lambda_up, 8), "lambda_down": round(lambda_down, 8),
            "filled_up": full_up, "filled_down": full_down,
            "best_bid": best_bid, "best_ask": best_ask, "levels": int(levels),
        }
        # جاهزية: الكمّيّة مُنفَّذة كاملةً على الجهتين + مستويات كافية.
        if not (full_up and full_down) or levels < self._required_levels:
            await self._context.publish(EVENT_OUT, {
                **head, "status": "insufficient_data",
                "confidence": round(evidence, 4), "weight_applied": 0.0,
                "ready": False, "analysis_state": STATE_NOT_READY,
                "state": STATE_NOT_READY, "metadata": meta})
            return
        denom = impact_up + impact_down
        # عدم التماثل: الجهة الأعلى انزلاقًا (الأرقّ) أقلّ مقاومةً ⇒ السعر يميل نحوها.
        #   عرضٌ رقيق (impact_up عالٍ) ⇒ صعوديّ · طلبٌ رقيق (impact_down عالٍ) ⇒ هبوطيّ.
        direction = max(-100.0, min(100.0, (impact_up - impact_down) / denom * 100.0)) \
            if denom > 0 else 0.0
        await self._context.publish(EVENT_OUT, {
            **head, "status": "ok",
            "signal": ("buy" if direction > 0 else "sell" if direction < 0 else "neutral"),
            "direction": round(direction, 4),
            "strength": round(abs(direction), 4),
            "confidence": round(evidence, 4),
            "weight_applied": self._weight,
            "ready": True, "analysis_state": STATE_READY, "state": STATE_READY,
            "metadata": meta})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"received": self._received, "published": self._published,
                   "rejected": self._rejected, "snapshots": self._snapshots,
                   "impact_qty": self._impact_qty}
        if self._received == 0:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_SNAPSHOTS, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="published=%d rejected=%d" % (self._published, self._rejected),
            details=details)
