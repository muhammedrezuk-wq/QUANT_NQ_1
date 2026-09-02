from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_OI = "market.oi"
EVENT_CANDLE = "market.candle"
EVENT_OUT = "sense.oi.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """العقود المفتوحة ورباعيّاتها — من دخل ومن خرج ومن أُعدم.

    holdVol معلومةُ تموضعٍ غير مشتقّة من السعر إطلاقًا: السعر يقول أين
    ذهبنا، والـOI يقول من قاد الحركة. تقارن كلُّ قراءةِ OI بسابقتها مع تغيّر
    السعر بينهما فتضع الحركة في رباعيّة:

        سعر↑ OI↑ = لونغات جديدة تقود ....... حركةٌ صادقة
        سعر↑ OI↓ = تغطيةُ شورتات .......... صعودٌ هشّ يُخفَّض وزنه
        سعر↓ OI↑ = شورتات جديدة تهاجم ..... هبوطٌ صادق
        سعر↓ OI↓ = تصفيةُ لونغات .......... شلّال/إعدام لا بيعَ اقتناع

    السعر من إغلاق آخر شمعة (market.candle)، والـOI من market.oi. أساسٌ
    لمقياس الوقود (الذرّة ١٧١) وكاشفٌ للتصفيات المحليّة. أرضيّةُ ضجيجٍ
    اختياريّة (noise_pct) تحت العتبة تُصنَّف الحركة flat. قراءة فقط — لا حساب
    اتجاهٍ ولا أمر."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._noise_pct = 0.0
        self._max_age_s = 60.0
        self._price: dict[str, float] = {}
        # symbol → (ts, price, oi) لآخر قراءةٍ صالحة (المرجع للرباعيّة التالية)
        self._prev: dict[str, tuple[float, float, float]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._oi_seen = 0
        self._candle_seen = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._noise_pct = float(context.config.get("noise_pct", 0.0))
        self._max_age_s = float(context.config.get("max_age_s", 60.0))
        context.subscribe(EVENT_OI, self._on_oi)
        context.subscribe(EVENT_CANDLE, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        close = _f(payload.get("close"))
        if not symbol or close is None or close <= 0:
            return
        self._price[symbol] = close                # آخر سعرٍ معلوم لأيّ إطار
        self._candle_seen += 1

    async def _on_oi(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        oi = _f(payload.get("oi"))
        if not symbol or oi is None or oi <= 0:
            return
        self._oi_seen += 1
        price = self._price.get(symbol)
        if price is None:
            return                                 # لا سعر بعد ⇒ لا رباعيّة
        now = time.time()
        prev = self._prev.get(symbol)
        self._prev[symbol] = (now, price, oi)
        if prev is None:
            return                                 # أوّل قراءةٍ صالحة = مرجعٌ فقط
        prev_ts, prev_price, prev_oi = prev
        d_oi = oi - prev_oi
        d_price = price - prev_price
        d_oi_pct = (d_oi / prev_oi * 100.0) if prev_oi else 0.0
        d_price_pct = (d_price / prev_price * 100.0) if prev_price else 0.0
        oi_up = d_oi_pct > self._noise_pct
        oi_dn = d_oi_pct < -self._noise_pct
        px_up = d_price_pct > self._noise_pct
        px_dn = d_price_pct < -self._noise_pct
        if px_up and oi_up:
            quadrant, honesty, label = "new_longs", "honest", "new longs leading"
        elif px_up and oi_dn:
            quadrant, honesty, label = "short_covering", "fragile", "short covering (fragile rise)"
        elif px_dn and oi_up:
            quadrant, honesty, label = "new_shorts", "honest", "new shorts attacking"
        elif px_dn and oi_dn:
            quadrant, honesty, label = "long_liquidation", "cascade", "long liquidation (cascade)"
        else:
            quadrant, honesty, label = "flat", "flat", "no significant change"
        state = {
            "provider": payload.get("provider"), "symbol": symbol,
            "oi": oi, "oi_prev": prev_oi,
            "price": price, "price_prev": prev_price,
            "d_oi": round(d_oi, 4), "d_oi_pct": round(d_oi_pct, 4),
            "d_price": round(d_price, 8), "d_price_pct": round(d_price_pct, 4),
            "over_s": round(now - prev_ts, 2), "over_min": round((now - prev_ts) / 60.0, 2),
            "quadrant": quadrant, "honesty": honesty, "label": label,
            "timestamp": now,
        }
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "oi_seen": self._oi_seen, "candle_seen": self._candle_seen,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "quadrant": {s: v["quadrant"] for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_OI", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="OI_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._state), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
