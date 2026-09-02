from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.2.0"
EVENT_IN = "crypto.decision.signal_card.state"
EVENT_OUT = "decision.approved.state"    # العقد المشترك — راجع الشرح لسبب اختيار هذا الاسم تحديدًا

STRATEGY_ID = "crypto_scalp_v1"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """محرك القرار — الذرّة الوحيدة في هذه الطبقة التي تلمس العقد المشترك.

    تنشر **حصرًا** `decision.approved.state` — لا `trading.final_decision`
    ولا `execution.*` ولا `platform.trade_event` أبدًا. السبب مزدوج:

    ١. **دقّةٌ منطقيّة:** هذا عرضٌ/قرارٌ، لا تنفيذٌ حقيقيّ — تنفيذك دائمًا
       بيدك على MEXC (قرارك الصريح، عرضٌ فقط بلا زرّ تأكيدٍ حاليًّا).
    ٢. **أمانٌ بنيويّ مُتحقَّقٌ من الكود:** `run_core.py:_verify_execution_
       safety_at_startup` يرفض إقلاع أي ذرّةٍ `startup_mode: auto` تنشر/
       تشترك حدثًا يبدأ بـ`execution.`/`trading.`/`broker.` لنطاق crypto
       كليًّا ("لا توجد سياسة تنفيذ معتمدة"). `decision.` لا يطابق أيًّا من
       الثلاث، فهذه الذرّة تُقلِع تلقائيًّا بأمان دون أي تعديلٍ على تلك
       السياسة — لأنها فعلًا ليست تنفيذًا.

    `decision.approved.state` وحده يكفي: 2707 (مخزن القرارات، النسخة
    الكريبتويّة الموجودة أصلًا) يستمع له وينشر صفًّا بـ`stage=APPROVED`،
    ويُقرَأ فورًا عبر `/gov/decisions` — هذا ما يُظهر "صفقة" على الداشبورد،
    بلا أي عملٍ إضافيّ على الداشبورد أو 2707 نفسها."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._max_age_s = 120.0
        self._published = 0
        self._skipped = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._max_age_s = float(context.config.get("max_age_s", 120.0))
        context.subscribe(EVENT_IN, self._on_sized)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _confidence(self, payload: dict[str, Any]) -> float:
        """تقديرٌ نوعيٌّ إرشاديّ من الرتبة والصنف — **ليس احتمالًا إحصائيًّا**
        (لا نموذج تنبّؤٍ مُدرَّب ولا سجلّ نتائج بعد — راجع الحدود)."""
        grade = payload.get("grade")
        entry_class = payload.get("entry_class")
        base = 0.6 if grade == "A" else 0.5 if grade == "B" else 0.45
        if entry_class == "②break_retest":
            base += 0.05   # "أوثق من الكسر نفسه" — 02-rules.md §٥②
        if payload.get("news_fresh"):
            base -= 0.05   # `02-rules.md` §٨: "تُخفَّض الثقة صراحةً" — الخبر وسمٌ لا حظر، تُقاس هنا فقط
        return round(min(max(base, 0.0), 0.75), 2)

    async def _on_sized(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = time.time()
        self._last_at = now
        symbol = str(payload.get("symbol") or "")
        direction = str(payload.get("direction") or "")
        if not symbol or direction not in ("long", "short") or payload.get("stop_loss") is None:
            self._skipped += 1
            return
        self._published += 1
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "direction": direction,
            "confidence": self._confidence(payload),
            "strategy_id": STRATEGY_ID,
            "entry_price": payload.get("entry_price"),
            "volume": None,
            "stop_loss": payload.get("stop_loss"),
            "take_profit": payload.get("take_profit"),
            "take_profit_2": payload.get("take_profit_2"),
            "take_profit_runner": payload.get("take_profit_runner"),
            "reason": "%s · %s%s · عرضٌ فقط — التنفيذ اليدوي على MEXC · ميزانية مخاطرةٍ $%s" % (
                payload.get("entry_class"), payload.get("grade"),
                " · 📰 خبرٌ طازج" if payload.get("news_fresh") else "", payload.get("max_risk_usd")),
            "metadata": {
                "max_risk_usd": payload.get("max_risk_usd"),
                "reference_equity_usd": payload.get("reference_equity_usd"),
                "competing_rank": payload.get("competing_rank"),
                "news_fresh": payload.get("news_fresh"), "news_age_min": payload.get("news_age_min"),
                "anchor": payload.get("anchor"),
                "entry_leg_high": payload.get("entry_leg_high"),
                "entry_leg_low": payload.get("entry_leg_low"),
                "stop_pct": payload.get("stop_pct"),
                "take_profit_source": payload.get("take_profit_source"),
                "take_profit_2_source": payload.get("take_profit_2_source"),
                "take_profit_runner_source": payload.get("take_profit_runner_source"),
                "grade_target_profile": payload.get("grade_target_profile"),
                "cancel_level": payload.get("cancel_level"),
                "time_stop_candles": payload.get("time_stop_candles"),
                "time_stop_deadline": payload.get("time_stop_deadline"),
                "r_multiple_price": payload.get("r_multiple_price"),
            },
            "timestamp": now,
        })

    async def health_check(self) -> HealthStatus:
        details = {"published": self._published, "skipped": self._skipped,
                   "age_s": (time.time() - self._last_at) if self._last_at else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_SIGNAL_CARD", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="SIGNAL_CARD_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="published=%d skipped=%d" % (self._published, self._skipped),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "published": self._published}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._published = int(state.get("published", 0))
