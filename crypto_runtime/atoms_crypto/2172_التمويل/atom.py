from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.funding"
EVENT_OUT = "sense.funding.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """معدل التمويل — استفتاءٌ نقديّ على الطرف المزدحم.

    الرسمُ الدوريّ (كلّ ٨ ساعات) الذي يدفعه الطرفُ المزدحم للطرف الآخر:
    موجبٌ مرتفع = لونغات محمومة تدفع (وقودُ هبوط)، سالب = شورتات مزدحمة
    (وقودُ عصرٍ صاعد). معلومةُ تموضعٍ غير مشتقّة من الشارت، وأبطأُ الحواسّ —
    لا يُتاجَر وحده؛ مُدخلٌ لتحديد الطرف المزدحم في مقياس الوقود (١٧١).

    التصنيف على funding_pct (المعدّل×١٠٠)، عتباتٌ معايَرة من توزيع ٥٣٩ يومًا:
        المحايد ≈ 0.004 (p50)
        > 0.010 (p90) ⇒ لونغات مزدحمة (اللونغ يدفع)
        < −0.003 (p10) ⇒ شورتات مزدحمة (الشورت يدفع)
    والدافعُ يتبع الإشارة: تمويلٌ موجب ⇒ اللونغ يدفع الشورت والعكس. قراءةٌ فقط."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._neutral = 0.004
        self._long = 0.010
        self._short = -0.003
        self._max_age_s = 60.0
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        c = context.config
        self._neutral = float(c.get("neutral", 0.004))
        self._long = float(c.get("long_thresh", 0.010))
        self._short = float(c.get("short_thresh", -0.003))
        self._max_age_s = float(c.get("max_age_s", 60.0))
        context.subscribe(EVENT_IN, self._on_funding)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_funding(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        rate = _f(payload.get("funding_rate"))
        pct = _f(payload.get("funding_pct"))
        if pct is None and rate is not None:
            pct = rate * 100.0
        if not symbol or pct is None:
            return
        if pct > self._long:
            bias = "longs_crowded"
        elif pct < self._short:
            bias = "shorts_crowded"
        else:
            bias = "neutral"
        pays = "longs_pay_shorts" if pct > 0 else "shorts_pay_longs" if pct < 0 else "balanced"
        now = time.time()
        state = {
            "provider": payload.get("provider"), "symbol": symbol,
            "funding_rate": rate, "funding_pct": round(pct, 6),
            "neutral": self._neutral,
            "distance_from_neutral": round(pct - self._neutral, 6),
            "bias": bias, "pays": pays, "interval": "8h",
            "timestamp": now,
        }
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "funding_pct": {s: v["funding_pct"] for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_FUNDING", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="FUNDING_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._state), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
