from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_OI = "market.oi"
EVENT_FUNDING = "market.funding"
EVENT_PREMIUM = "market.premium"
EVENT_CANDLE = "market.candle"
EVENT_OUT = "sense.fuel.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """مقياس وقود الشلّال ثنائيّ الاتجاه — أولويّة القياس الأولى.

    يقيس القابليّة للاشتعال قبل الشرارة: تكدّسُ مراكزَ (OI↑) ضدّ اتجاه السعر
    = وقودُ تصفيةٍ جماعيّة إن انكسر مستوى. لا يتنبّأ باللحظة — يقيس كمّيّة
    البارود، ويحدّد جهته من الطرف المزدحم (تمويلٌ/علاوة).

    على نافذةٍ ~٣٠ دقيقة (سجلٌّ تراكميّ من market.oi وسعرٌ من market.candle)،
    المرجعُ أحدثُ صفٍّ بلغ عمرُه النافذة (وإلا الأقدم المتاح):
        Δ OI٪ = oi_pct ·  Δ السعر٪ = px_pct  منذ المرجع
        الطرف المزدحم: تمويل > 0.010 أو علاوة > −3 ⇒ لونغات مزدحمة
                       تمويل < −0.003 أو علاوة < −7 ⇒ شورتات مزدحمة · غيرُه متوازن
        OI٪ > +0.15 وسعر٪ < −0.1 ⇒ FUEL BUILDING into decline [الطرف] (خطر شلّال هابط)
        OI٪ > +0.15 وسعر٪ > +0.1 ⇒ FUEL BUILDING into rise [الطرف] (عصرٌ صاعد إن الشورتات مزدحمة)
        OI٪ < −0.5              ⇒ FUEL SPENT (تفريغٌ/تصفية تمّت — البارود احترق)
        غيرُه                   ⇒ neutral [الطرف]

    العتبات في manifest — معايَرة من توزيعاتٍ مقيسة (٢٠٢٦-٠٨-٢٥): التمويل
    ٥٣٩ يومًا p50=0.004/p90=0.010/p10=−0.003 · العلاوة ١٨٠ يومًا p90=−3/p10=−7
    (نظامٌ سالبٌ كلُّه). القيم على funding_pct (المعدّل×١٠٠) وعلى premium_bps.
    قراءةٌ ومقياسٌ فقط — لا أمر. حدُّه الصادق: يقيس القابليّة لا التوقيت."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_s = 1800.0
        self._oi_build = 0.15
        self._px_move = 0.1
        self._spent = -0.5
        self._long_funding = 0.010
        self._short_funding = -0.003
        self._premium_hot = -3.0
        self._premium_short = -7.0
        self._max_age_s = 60.0
        self._max_log = 4000
        self._price: dict[str, float] = {}
        self._funding_pct: dict[str, float] = {}
        self._premium_bps: dict[str, float] = {}
        self._log: dict[str, deque] = {}           # symbol → deque[(ts, price, oi)]
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        c = context.config
        self._window_s = float(c.get("window_s", 1800.0))
        self._oi_build = float(c.get("oi_build_pct", 0.15))
        self._px_move = float(c.get("px_move_pct", 0.1))
        self._spent = float(c.get("spent_pct", -0.5))
        self._long_funding = float(c.get("long_funding", 0.010))
        self._short_funding = float(c.get("short_funding", -0.003))
        self._premium_hot = float(c.get("premium_hot_bps", -3.0))
        self._premium_short = float(c.get("premium_short_bps", -7.0))
        self._max_age_s = float(c.get("max_age_s", 60.0))
        self._max_log = int(c.get("max_log_rows", 4000))
        context.subscribe(EVENT_OI, self._on_oi)
        context.subscribe(EVENT_FUNDING, self._on_funding)
        context.subscribe(EVENT_PREMIUM, self._on_premium)
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
        if symbol and close is not None and close > 0:
            self._price[symbol] = close

    async def _on_funding(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        pct = _f(payload.get("funding_pct"))
        if pct is None:
            rate = _f(payload.get("funding_rate"))
            pct = rate * 100.0 if rate is not None else None
        if symbol and pct is not None:
            self._funding_pct[symbol] = pct

    async def _on_premium(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        bps = _f(payload.get("premium_bps"))
        if symbol and bps is not None:
            self._premium_bps[symbol] = bps

    def _crowd(self, symbol: str) -> str:
        """الطرف المزدحم — العتبات نفسها التي في mexc_read (المعايَرة، لا ورقة
        قديمة). قيمةٌ غائبة لا تُرجِّح جهتها (لا توقّع — قِس)."""
        f = self._funding_pct.get(symbol)
        p = self._premium_bps.get(symbol)
        longs = (f is not None and f > self._long_funding) or \
                (p is not None and p > self._premium_hot)
        shorts = (f is not None and f < self._short_funding) or \
                 (p is not None and p < self._premium_short)
        if longs:
            return "longs_crowded"
        if shorts:
            return "shorts_crowded"
        return "balanced"

    async def _on_oi(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        oi = _f(payload.get("oi"))
        if not symbol or oi is None or oi <= 0:
            return
        price = self._price.get(symbol)
        if price is None:
            return                                 # لا سعر بعد ⇒ لا وقود
        now = time.time()
        log = self._log.setdefault(symbol, deque())
        target = now - self._window_s
        ref: tuple[float, float, float] | None = None
        for row in log:                            # السجلّ زمنيّ ⇒ أحدثُ صفٍّ ≥ النافذة
            if row[0] <= target:
                ref = row
            else:
                break
        if ref is None and log:
            ref = log[0]                           # لا صفّ بلغ النافذة بعد ⇒ الأقدم
        log.append((now, price, oi))
        while len(log) > self._max_log:
            log.popleft()
        cutoff = now - (self._window_s * 2.0 + 300.0)
        while len(log) > 2 and log[0][0] < cutoff:
            log.popleft()
        crowd = self._crowd(symbol)
        funding = self._funding_pct.get(symbol)
        premium = self._premium_bps.get(symbol)
        if ref is None:
            state = {
                "provider": payload.get("provider"), "symbol": symbol,
                "oi": oi, "price": price,
                "oi_pct": None, "px_pct": None, "window_min": None,
                "funding_pct": funding, "premium_bps": premium, "crowd": crowd,
                "fuel": "baseline", "risk": "warming",
                "label": "baseline saved — gauge active from next reading",
                "timestamp": now,
            }
        else:
            ref_ts, ref_price, ref_oi = ref
            oi_pct = (oi - ref_oi) / ref_oi * 100.0 if ref_oi else 0.0
            px_pct = (price - ref_price) / ref_price * 100.0 if ref_price else 0.0
            window_min = (now - ref_ts) / 60.0
            if oi_pct > self._oi_build and px_pct < -self._px_move:
                fuel, risk = "building_decline", "down_cascade"
                label = "FUEL BUILDING into decline [%s] -> DOWN-cascade risk" % crowd
            elif oi_pct > self._oi_build and px_pct > self._px_move:
                fuel = "building_rise"
                if crowd == "shorts_crowded":
                    risk = "up_squeeze"
                    tail = "UP-squeeze risk (shorts fighting the move)"
                else:
                    risk = "over_crowding"
                    tail = "longs driving; over-crowding watch"
                label = "FUEL BUILDING into rise [%s] -> %s" % (crowd, tail)
            elif oi_pct < self._spent:
                fuel, risk = "spent", "spent"
                label = "FUEL SPENT (deleveraged / liquidation done)"
            else:
                fuel, risk = "neutral", "neutral"
                label = "neutral [%s]" % crowd
            state = {
                "provider": payload.get("provider"), "symbol": symbol,
                "oi": oi, "price": price,
                "oi_pct": round(oi_pct, 3), "px_pct": round(px_pct, 3),
                "window_min": round(window_min, 1),
                "funding_pct": funding, "premium_bps": premium, "crowd": crowd,
                "fuel": fuel, "risk": risk, "label": label,
                "timestamp": now,
            }
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "fuel": {s: v["fuel"] for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_OI", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="FUEL_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._state), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
