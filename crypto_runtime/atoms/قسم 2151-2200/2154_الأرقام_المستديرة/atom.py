from __future__ import annotations

import math
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.2.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.round_numbers.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _decade(price: float) -> float:
    """عَشْرية السعر — أكبر قوّة عشرة لا تتجاوزه.

    ٢٠٢٦-٠٩-٠١ (حكم المالك: «في خلل للعملات الصغيرة… ما بتطلع أسعارها
    الحقيقيّة»). الخطوات كانت ثابتة مطلقة (1000/500/100) وتُطبَّق على **كل**
    رمز مهما كان مقياسه — وهي أرقام مفصّلة لسعر بحجم البِتكوين. على عملة
    سعرها 0.00001234 يصير `price // 1000 == 0`، فتخرج المستويات الثلاثة
    كلّها {below: 0, above: 1000} — أي مستوًى نفسيّ لا معنى له، وشاهدٌ يدخل
    التجمّع وهو فارغ. والتوثيق نفسه كان يقول «الخطوات قابلة للمعايرة حسب
    مقياس سعر الرمز» — لكن المعايرة كانت قيمة واحدة للجميع.
    فتُشتقّ الخطوة من مقياس السعر نفسه."""
    if price <= 0:
        return 1.0
    return 10.0 ** math.floor(math.log10(price))


def _round_to_step(value: float, step: float) -> float:
    """تدوير يتبع دقّة الخطوة لا رقمين عشريّين ثابتين.

    `round(level, 2)` كان يسحق أي مستوى دون السنت: مستوى 0.0000120 يصير
    0.0، ومسافةٌ حقيقيّة 0.0000014 تصير 0.0 — فيبدو السعر ملتصقًا بالمستوى
    وهو ليس كذلك. الدقّة تُشتقّ من الخطوة: خطوةٌ 1e-5 تعني خمس خانات."""
    if step <= 0:
        return value
    digits = max(0, min(12, -math.floor(math.log10(step)) + 2))
    return round(value, digits)


def _bracket(price: float, step: float) -> dict[str, float]:
    """أقرب مستويين مستديرين (تحت/فوق) والأقرب منهما ومسافته."""
    below = (price // step) * step
    above = below + step
    nearest = below if (price - below) <= (above - price) else above
    return {"below": _round_to_step(below, step), "above": _round_to_step(above, step),
            "nearest": _round_to_step(nearest, step),
            "dist": _round_to_step(abs(price - nearest), step),
            "step": _round_to_step(step, step)}


class Atom(AtomBase):
    """الأرقام المستديرة — مستويات نفسية/أوامر.

    البشر والخوارزميّات تضع الأوامر على الأرقام الكاملة: وقفاتٌ تحت الألف،
    أهدافٌ عنده، سيولةٌ معلّقة حوله (رُصدت جدرانًا في الدفتر). ليست سحرًا بل
    تكدّس أوامر قابل للرصد. الكبرى (×الخطوة) دائمًا في الخريطة، والوسطى
    سياقًا. **شاهدٌ معزِّز لا يُتاجَر وحده** — قيمتها في التجمّع مع سبب آخر.
    الخطوات قابلة للمعايرة حسب مقياس سعر الرمز."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._max_age_s = 600.0
        self._major = 1000.0     # الكبرى — دائمًا في الخريطة
        self._mid = 500.0        # الوسطى — سياقيّة
        self._minor = 100.0      # الصغرى — لا تُتاجَر وحدها أبدًا
        self._auto_scale = True  # الخطوة تتبع مقياس سعر الرمز
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeframe = str(context.config.get("timeframe", "5m"))
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        self._major = float(context.config.get("major_step", 1000.0))
        self._mid = float(context.config.get("mid_step", 500.0))
        self._minor = float(context.config.get("minor_step", 100.0))
        # الافتراضي: الخطوة تتبع مقياس الرمز. من يريد خطوات مطلقة يضبط
        # `auto_scale: false` فتُحترم قيمه كما هي (سلوك 1.1.0 حرفيًّا).
        self._auto_scale = bool(context.config.get("auto_scale", True))
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if str(payload.get("timeframe")) != self._timeframe:
            return
        symbol = str(payload.get("symbol") or "")
        price = _f(payload.get("close"))
        if not symbol or price is None or price <= 0:
            return
        now = time.time()
        if self._auto_scale:
            # الكبرى = عُشر عَشْرية السعر — أي ثلاثة أرقام معنويّة، وهو ما
            # يفعله الناس فعلًا. والنتيجة على مقياس البتكوين هي **عيار
            # المالك نفسه حرفيًّا**: سعر 80,500 ⇒ 1000/500/100 كما في
            # المانيفست — فالقاعدة تعمّم العيار ولا تنقضه.
            # تُحسب كقوّة عشرة مباشرة لا بقسمة: `1e-5 / 10` يعطي
            # 1.0000000000000002e-06 بضجيج الفاصلة العائمة، وهذه
            # الخطوة تُنشَر في الحمولة فيقرأها المالك.
            unit = _decade(price) * 0.1 if price <= 0 else 10.0 ** (math.floor(math.log10(price)) - 1)
            major, mid, minor = unit, unit / 2.0, unit / 10.0
        else:
            major, mid, minor = self._major, self._mid, self._minor
        state = {"provider": payload.get("provider"), "symbol": symbol, "price": price,
                 "auto_scale": self._auto_scale,
                 "major": _bracket(price, major),
                 "mid": _bracket(price, mid),
                 "minor": _bracket(price, minor),
                 "role": "confluence_only",   # معزِّز لا يُتاجَر وحده
                 "timestamp": now}
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "auto_scale": self._auto_scale,
                   "steps": {"major": self._major, "mid": self._mid, "minor": self._minor}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_CANDLE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CANDLE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._state), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
