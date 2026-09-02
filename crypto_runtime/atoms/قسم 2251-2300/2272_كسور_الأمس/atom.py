from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN_PRIOR = "sense.prior_day.state"
EVENT_IN_CANDLE = "market.candle"
EVENT_OUT = "crypto.decision.breaks.state"

STAGE_NONE = "none"
STAGE_BROKEN = "broken"              # إغلاقٌ خلف المستوى — الصنف ② بانتظار إعادة الاختبار
STAGE_RETESTED = "retested"          # لمسٌ عائدٌ للمستوى دون فقدان الإغلاق خلفه
STAGE_CONFIRMED = "confirmed"        # صمد بعد اللمس ⇒ بطاقة الصنف ② تُعرَض على 2274


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """كسور الأمس — آلة حالة الكسر/إعادة الاختبار لـPDH وPDL (مرشّحا الصنفين ②③).

    `scalping/02-rules.md` §٥②: "إغلاق ٥د خلف المستوى ⇒ انتظار العودة لاختباره
    من الجهة الأخرى وصمودها ⇒ معلّق في منطقة الاختبار. أوثق من الكسر نفسه."
    لكل رمزٍ ولكل مستوى (PDH صعودًا، PDL هبوطًا) آلة حالةٍ مستقلّة:
    none → broken (إغلاقٌ خلفه) → retested (لمسٌ عائدٌ بلا فقدان الإغلاق) →
    confirmed (إغلاقٌ يصمد بعد اللمس ⇒ الحدث يُنشَر). فقدان الإغلاق خلف
    المستوى في أي مرحلةٍ (كسرٌ فاشل) يُعيد الحالة لـnone.

    **حدٌّ صريح — لا تُصدر حكم الصنف ③ (الكسر المُرشَّح) بنفسها:** شرطاه
    الإضافيان (الوقود FUEL BUILDING، والمسافة ≤١٥٠ نقطة من `02-rules.md` §٥③)
    يحتاجان `sense.fuel.state`(2171) ومسافة القفزة — تُنشَر هنا خامًا
    (`distance_points`) ليُطبَّق الفلتران في مُصنِّف الدخول 2274 حيث تجتمع كل
    الحواسّ، لا هنا. كذلك تمييز الكسر الهادئ/الصاخب (§٥ ذيل، حجمٌ ≥×3 سالبٌ
    مقاسًا) يحتاج `sense.avg_volume.state`(2157) للمقارنة — يُنشَر `volume`
    خامًا لنفس السبب."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._touch_tolerance_pct = 0.05    # افتراضيّ كريبتويّ غير موثَّق — غير مذكور رقميًّا
                                             # في scalping/ لتعريف "لمس" إعادة الاختبار نفسه
        self._max_age_s = 600.0
        # symbol -> {"pdh","pdl"}
        self._levels: dict[str, dict[str, float]] = {}
        # symbol -> {"up": {...}, "dn": {...}}
        self._state: dict[str, dict[str, dict[str, Any]]] = {}
        self._updates = 0
        self._confirmations = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeframe = str(context.config.get("timeframe", "5m"))
        self._touch_tolerance_pct = float(context.config.get("touch_tolerance_pct", 0.05))
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        context.subscribe(EVENT_IN_PRIOR, self._on_prior)
        context.subscribe(EVENT_IN_CANDLE, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_prior(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or not payload.get("prior_ready"):
            return
        symbol = str(payload.get("symbol") or "")
        pdh = _f(payload.get("pdh")); pdl = _f(payload.get("pdl"))
        if not symbol or pdh is None or pdl is None:
            return
        self._levels[symbol] = {"pdh": pdh, "pdl": pdl}

    def _tracker(self, symbol: str) -> dict[str, dict[str, Any]]:
        return self._state.setdefault(symbol, {
            "up": {"stage": STAGE_NONE, "break_price": None, "break_at": None},
            "dn": {"stage": STAGE_NONE, "break_price": None, "break_at": None},
        })

    def _advance(self, tr: dict[str, Any], *, held: bool, touched: bool, price: float, now: float) -> str | None:
        """يُقدّم آلة حالةٍ واحدة (up أو dn) خطوةً؛ يُرجع الحدث إن وُلد واحد."""
        stage = tr["stage"]
        if not held:
            if stage != STAGE_NONE:
                tr["stage"] = STAGE_NONE
                tr["break_price"] = None
                tr["break_at"] = None
            return None
        if stage == STAGE_NONE:
            tr["stage"] = STAGE_BROKEN
            tr["break_price"] = price
            tr["break_at"] = now
            return "broken"
        if stage == STAGE_BROKEN and touched:
            tr["stage"] = STAGE_RETESTED
            return "retested"
        if stage == STAGE_RETESTED:
            tr["stage"] = STAGE_CONFIRMED
            return "confirmed"
        return None

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if str(payload.get("timeframe")) != self._timeframe:
            return
        symbol = str(payload.get("symbol") or "")
        high = _f(payload.get("high")); low = _f(payload.get("low"))
        close = _f(payload.get("close")); volume = _f(payload.get("volume"))
        if not symbol or None in (high, low, close):
            return
        levels = self._levels.get(symbol)
        if levels is None:
            return
        pdh = levels["pdh"]; pdl = levels["pdl"]
        tol_up = pdh * self._touch_tolerance_pct / 100.0
        tol_dn = pdl * self._touch_tolerance_pct / 100.0
        now = time.time()
        tr = self._tracker(symbol)
        up_event = self._advance(tr["up"], held=close > pdh, touched=low <= pdh + tol_up,
                                  price=close, now=now)
        dn_event = self._advance(tr["dn"], held=close < pdl, touched=high >= pdl - tol_dn,
                                  price=close, now=now)
        self._updates += 1
        self._last_at = now
        for level_name, level_value, event in (("pdh", pdh, up_event), ("pdl", pdl, dn_event)):
            if event is None:
                continue
            if event == "confirmed":
                self._confirmations += 1
            distance_points = close - level_value if level_name == "pdh" else level_value - close
            await self._context.publish(EVENT_OUT, {
                "provider": payload.get("provider"), "symbol": symbol,
                "level": level_name, "level_value": level_value, "event": event,
                "distance_points": round(distance_points, 8),
                "price": close, "volume": volume,
                "pdh": pdh, "pdl": pdl, "timestamp": now,
            })

    async def health_check(self) -> HealthStatus:
        details = {"symbols_with_levels": len(self._levels), "updates": self._updates,
                   "confirmations": self._confirmations,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "stages": {s: {"up": v["up"]["stage"], "dn": v["dn"]["stage"]}
                              for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_CANDLE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CANDLE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d confirmations=%d" % (
                                len(self._levels), self._updates, self._confirmations),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates, "confirmations": self._confirmations}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
            self._confirmations = int(state.get("confirmations", 0))
