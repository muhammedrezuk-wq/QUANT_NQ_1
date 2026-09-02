from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.premium"
EVENT_OUT = "sense.premium.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """علاوة العقد على المؤشر — ميزانُ حرارة الرافعة لحظةً بلحظة.

    انحرافُ سعر عقد MEXC عن مؤشر السبوت العالميّ بالنقاط الأساس (التمويلُ
    نسخته البطيئة): موجبٌ متمدّد = عقدٌ يشتريه مرفوعون بحماسة؛ سالبٌ متعمّق
    = ذعرُ بيعٍ في العقد. مُدخلٌ ثانٍ لتحديد الطرف المزدحم (مقياس الوقود ١٧١).

    التصنيف على premium_bps في نظامٍ سالبٍ كلّه (توزيع ١٨٠ يومًا):
        > −3 (p90) ⇒ ساخنة/لونغات مزدحمة
        < −7 (p10) ⇒ ذعرٌ/شورتات مزدحمة
        بينهما     ⇒ محايدة
    وقراءتُها الأذكى «من يقود؟»: مؤشرٌ يصعد قبل العقد = شراءُ سبوتٍ حقيقيّ
    يقود (أصحّ التعافي) — يُقاس من عيّنتين متتاليتين (فرق المؤشر مقابل فرق
    العادل)، والاتجاهُ recovering/deepening. لقطةٌ شديدة التذبذب — القيمة في
    مستواها المستمرّ لا في وخزة. قراءةٌ فقط."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._hot = -3.0
        self._short = -7.0
        self._eps = 0.1
        self._max_age_s = 60.0
        # symbol → (premium_bps, index, fair) لآخر عيّنة (للاتجاه والقيادة)
        self._prev: dict[str, tuple[float, float | None, float | None]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        c = context.config
        self._hot = float(c.get("premium_hot_bps", -3.0))
        self._short = float(c.get("premium_short_bps", -7.0))
        self._eps = float(c.get("trend_eps_bps", 0.1))
        self._max_age_s = float(c.get("max_age_s", 60.0))
        context.subscribe(EVENT_IN, self._on_premium)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_premium(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        bps = _f(payload.get("premium_bps"))
        if not symbol or bps is None:
            return
        fair = _f(payload.get("fair_price"))
        index = _f(payload.get("index_price"))
        if bps > self._hot:
            tier, crowd = "hot", "longs_crowded"
        elif bps < self._short:
            tier, crowd = "panic", "shorts_crowded"
        else:
            tier, crowd = "neutral", "balanced"
        prev = self._prev.get(symbol)
        trend = "flat"
        leader = "flat"
        if prev is not None:
            prev_bps, prev_index, prev_fair = prev
            if bps > prev_bps + self._eps:
                trend = "recovering"                   # نحو الصفر (تعافٍ)
            elif bps < prev_bps - self._eps:
                trend = "deepening"                    # يتعمّق سالبًا (ذعر)
            if (index is not None and prev_index is not None
                    and fair is not None and prev_fair is not None):
                d_index = index - prev_index
                d_fair = fair - prev_fair
                eps_p = max(abs(index), 1.0) * 1e-7
                if abs(d_index) < eps_p and abs(d_fair) < eps_p:
                    leader = "flat"
                elif d_index >= d_fair:
                    leader = "index"                   # السبوت يقود (أصحّ تعافٍ)
                else:
                    leader = "perp"                    # الرافعة تقود
        self._prev[symbol] = (bps, index, fair)
        now = time.time()
        state = {
            "provider": payload.get("provider"), "symbol": symbol,
            "premium_bps": round(bps, 3), "fair_price": fair, "index_price": index,
            "tier": tier, "crowd": crowd, "trend": trend, "leader": leader,
            "timestamp": now,
        }
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "premium_bps": {s: v["premium_bps"] for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_PREMIUM", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="PREMIUM_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._state), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
