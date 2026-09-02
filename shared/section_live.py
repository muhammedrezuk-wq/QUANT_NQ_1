"""ورقة التنفيذ §٣ — المصدر الحيّ لأقسام 200 · 250 · 300 · 350.

لماذا نواة مشتركة واحدة؟
    الأقسام الأربعة تحتاج السلوك نفسه: تِكّة صالحة ⇒ تحديث الأدلّة
    الداخلية ⇒ إعادة حساب ⇒ عمق ⇒ ثقة ⇒ حالة ⇒ نشر. تكرارها أربع مرّات
    يعيد عطل «٦٧ بانيًا محلّيًّا» بشكل آخر: أربع نسخ تتباعد مع الوقت.
    ⇒ التعريف هنا وحده (§٥٢).

⛔ ما **لا** تفعله هذه النواة:
    لا تخترع رأيًا للقسم. اتّجاه القسم وقوّته يبقيان من وحداته — هي
    مجاله. التِكّة تُحدّث **العمق والثقة والحداثة والحالة**، أي:
    «هل يحقّ لأحد أن يستعمل هذه النتيجة الآن؟» لا «ما رأي القسم؟».
    الشمعة تبقى سياقًا تاريخيًّا؛ لكنّها لم تعد بوّابة إجبارية.

⛔ الهوية: `account + broker + symbol` (§٢ · §٣٠). حمولة ناقصة تُرفض
    ولا تُكمَّل من ذاكرة سابقة.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import clock

from shared.financial_scope import account_broker
from shared.live_analysis import AnalysisSettingsStore
from shared.section_contract import stale_after_s
from shared.unified_contract import (
    STATE_ANALYZING, STATE_ERROR, STATE_INVALID, STATE_NOT_READY, STATE_READY,
    STATE_STALE,
)

EVENT_TICK = "market.tick.validated"
EVENT_SECOND = "SYS_SECOND"
# §٣٠ — ملكيّة الحساب: 619 هو المصدر الوحيد لاسم الوسيط.
EVENT_ACCOUNT = "platform.account.state"

#: عمق مطلوب افتراضيّ للقسم حتى تُعاير من اللوحة (§١٢ من ورقة الحلول).
DEFAULT_REQUIRED_DEPTH = 60.0
#: بعده تُعدّ آخر نتيجة قديمة (§١٤).
DEFAULT_TTL_S = 5.0
_WINDOW = 64


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass
class SectionLiveState:
    """أدلّة التِكّة لقسم واحد على أصل واحد لحساب/وسيط واحد."""

    prices: deque[float] = field(default_factory=lambda: deque(maxlen=_WINDOW))
    returns: deque[float] = field(default_factory=lambda: deque(maxlen=_WINDOW - 1))
    spreads: deque[float] = field(default_factory=lambda: deque(maxlen=_WINDOW))
    timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=_WINDOW))
    sequence: int = 0
    last_tick_ts: float = 0.0
    #: آخر نتائج وحدات القسم — سياق الشمعة، يُحدَّث عند اكتمال الدورة.
    units: dict[str, Any] = field(default_factory=dict)
    expected_units: int = 0
    cycle_id: str = ""
    timeframe: str = ""
    #: NQ seal item 22 (A8): directional read of the last observed cycle,
    #: kept only when the cycle payload actually measured it. None means
    #: unknown -- the card omits the field and the contract declares it.
    direction: float | None = None
    strength: float | None = None


#: §٢٠ — الذرّات الإلزامية لكل قسم. ما ليس هنا **اختياريّ**: غيابه لا
#: يمنع الجاهزية، وحضوره يُحسب. مُعلَنة صراحةً كي لا يُستنتج الإلزام.
#: الإلزاميّات بأسماء الوحدات كما تصل في `results` بدورة القسم — مقيسة حيًّا
#: ٢٠٢٦-٠٨-٢١: `structure.cycle.collected` مفاتيحها `swing · external ·
#: internal · bos · choch · mss · structure_trend · phase`، و`stats` فيها
#: `mean · std · zscore`، و`probability` فيها `trend_model · models_merged ·
#: confidence_aggregator`. فالأسماء هنا **مطابقة للسلك**، ومحاولة تحويلها
#: إلى أرقام ذرّات كانت خطأً صُحّح بالقياس.
REQUIRED_UNITS: dict[str, frozenset[str]] = {
    "200": frozenset({"swing", "external", "internal", "structure_trend"}),
    "250": frozenset({"pool", "buyside", "sellside"}),
    "300": frozenset({"mean", "std", "zscore"}),
    "350": frozenset({"trend_model", "models_merged", "confidence_aggregator"}),
}

_UNIT_STATE_ORDER = (STATE_ERROR, STATE_INVALID, STATE_STALE, STATE_NOT_READY)


def unit_opinion(units: dict[str, Any]) -> dict[str, Any]:
    """رأي القسم من وحداته — متوسّط موزون لما **قاسته** الوحدات فعلًا.

    (٢٠٢٦-٠٨-٢٥ · بند «الأقسام تكبّ تحليل وحداتها»): ٣٬٨٤٤ نتيجة وحدة في
    ٥٠ ثانية ولا واحدة كانت تدخل البطاقة — القسم كان يعدّ من قال «تمام»
    ويحسب ثقته من جودة التِكّة. الآن اتجاه القسم وقوّته وثقته تُشتقّ من
    القيم الموحّدة التي نشرتها وحداته، موزونةً بوزن الوحدة إن قِيس وإلّا
    بحصص متساوية بين الوحدات التي قاست — طريقة تجميع معلَنة، لا اختراع
    قيمة (A8): وحدة لم تقس حقلًا لا تدخل متوسّطه، وقسم بلا وحدة قاست
    يبقى الحقل فيه غائبًا لا صفرًا.
    """
    sums = {"direction": 0.0, "strength": 0.0, "confidence": 0.0}
    masses = {"direction": 0.0, "strength": 0.0, "confidence": 0.0}
    measured_units = 0
    for unit in units.values():
        if not isinstance(unit, dict):
            continue
        block = unit.get("unified")
        block = block if isinstance(block, dict) else unit
        unknown = set(block.get("unknown_fields") or [])
        weight = _finite(block.get("weight"))
        weight = weight if weight is not None and weight > 0 else 1.0
        touched = False
        for field in sums:
            if field in unknown:
                continue
            value = _finite(block.get(field))
            if value is None:
                continue
            sums[field] += value * weight
            masses[field] += weight
            touched = True
        if touched:
            measured_units += 1
    return {
        field: (round(sums[field] / masses[field], 4)
                if masses[field] > 0 else None)
        for field in sums
    } | {"measured_units": measured_units}


def _unit_state(unit: Any) -> str:
    """حالة وحدة واحدة كما أعلنتها هي — بلا تخمين."""
    if not isinstance(unit, dict):
        return STATE_INVALID
    declared = str(unit.get("state") or "").strip().upper()
    if declared:
        return declared
    status = str(unit.get("status") or "").strip().lower()
    if status in ("error", "failed"):
        return STATE_ERROR
    if status in ("invalid", "rejected"):
        return STATE_INVALID
    if status == "stale":
        return STATE_STALE
    if status in ("insufficient_data", "warming_up", "not_ready"):
        return STATE_NOT_READY
    return STATE_READY if status == "ok" else STATE_ANALYZING


def required_state(section_id: str, units: dict[str, Any]) -> tuple[str, list[str]]:
    """§٢١ — حالة القسم من ذرّاته الإلزامية وحدها.

    ⛔ لا يُعلَن القسم `READY` وذرّة إلزامية غير جاهزة. وأسوأ حالة بين
       الإلزاميّات هي حالة القسم: `ERROR` ثمّ `INVALID` ثمّ `STALE` ثمّ
       `NOT_READY`. والغائب الإلزاميّ ⇒ `NOT_READY` باسمه معلَنًا.
    """
    required = REQUIRED_UNITS.get(str(section_id))
    if not required:
        return "", []
    missing = sorted(uid for uid in required if uid not in units)
    if missing:
        return STATE_NOT_READY, missing
    states = {uid: _unit_state(units[uid]) for uid in required}
    for blocking in _UNIT_STATE_ORDER:
        offenders = sorted(uid for uid, value in states.items() if value == blocking)
        if offenders:
            return blocking, offenders
    return STATE_READY, []


class SectionLiveKernel:
    """يجعل القسم يتحدّث من التِكّة لا من إغلاق الشمعة."""

    def __init__(self, section_id: str, event_out: str) -> None:
        self.section_id = str(section_id)
        self.event_out = event_out
        self.context: Any = None
        self.running = False
        self.states: dict[tuple[str, str, str], SectionLiveState] = {}
        self.required_depth = DEFAULT_REQUIRED_DEPTH
        self.ttl_s = DEFAULT_TTL_S
        self.broker_by_account: dict[str, str] = {}
        self.ticks = self.published = self.invalid = self.stale_published = 0
        # §١٢ — العيار لكل `account+broker+symbol+section`، لا رقم عالميّ.
        self.settings = AnalysisSettingsStore()
        self._calibration: dict[tuple[str, str, str], dict[str, Any]] = {}

    def calibration(self, scope: tuple[str, str, str]) -> dict[str, Any]:
        """عيار هذا النطاق وحده. غيابه من المخزن ⇒ الافتراض المعلَن."""
        cached = self._calibration.get(scope)
        if cached is None:
            cached = self.settings.get(scope[0], scope[1], scope[2], self.section_id)
            self._calibration[scope] = cached
        return cached

    def required_depth_for(self, scope: tuple[str, str, str]) -> float:
        value = _finite(self.calibration(scope).get("required_depth"))
        return self.required_depth if value is None else _clip(value)

    def ttl_now(self) -> float:
        """أفق الطزاجة الساري — عيار المالك المعتمد ما لم يُضبط حدّ خاص.

        (٢٠٢٦-٠٨-٢٥ · «مفتاح بلا سلك»): كان `5.0` محفورًا هنا بينما اعتماد
        المالك `10.01` بلا قارئ. حدٌّ خاص ضُبط على النسخة يبقى نافذًا."""
        return self.ttl_s if self.ttl_s != DEFAULT_TTL_S else stale_after_s()

    # ── دورة الحياة ──────────────────────────────────────────────────────
    async def initialize(self, context: Any) -> None:
        self.context = context
        context.subscribe(EVENT_TICK, self.on_tick)
        context.subscribe(EVENT_SECOND, self.on_second)
        context.subscribe(EVENT_ACCOUNT, self.on_account)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    # ── الهوية ───────────────────────────────────────────────────────────
    def scope_of(self, payload: Any) -> tuple[str, str, str] | None:
        """§٣٠ — حساب + وسيط + أصل.

        ⛔ التِكّة الحقيقية تحمل `account_id` و`provider` ولا تحمل `broker`.
           و`provider` (`CTRADER`/`MT5`) مصدرُ تغذية لا وسيط؛ الوسيط
           (`Raw Trading Ltd`) يصل من `619`. مساواتهما تزوير حقل (§٢٧).
        ✅ يُحلّ الوسيط من ملكيّة الحساب بآليّة المشروع المعتمدة، وحسابٌ
           بلا وسيط معلَن يُرفض ويُعدّ — ولا يُخمَّن (§٢).
        """
        if not isinstance(payload, dict):
            return None
        owner = account_broker(payload, self.broker_by_account)
        if owner is None:
            return None
        symbol = str(payload.get("symbol") or payload.get("asset") or "").strip().upper()
        return (owner[0], owner[1], symbol) if symbol else None

    async def on_account(self, payload: dict[str, Any]) -> None:
        """ملكيّة الحساب من `619` — المصدر الوحيد لاسم الوسيط."""
        if not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        if account and broker:
            self.broker_by_account[account] = broker

    # ── سياق الشمعة: نتائج وحدات القسم ───────────────────────────────────
    def observe_cycle(self, payload: dict[str, Any]) -> None:
        """يلتقط آخر دورة مكتملة كسياق تاريخيّ — لا ينشر ولا يفتح دورة."""
        scope = self.scope_of(payload)
        if scope is None:
            return
        state = self.states.setdefault(scope, SectionLiveState())
        results = payload.get("results")
        state.units = dict(results) if isinstance(results, dict) else {}
        expected = _finite(payload.get("expected"))
        state.expected_units = int(expected) if expected else len(state.units)
        state.cycle_id = str(payload.get("cycle_id") or "")
        state.timeframe = str(payload.get("timeframe") or "")
        # NQ seal item 22 (A8): pass measured directional values through to
        # the live card. Absent or non-numeric stays None (reset each cycle)
        # -- nothing is invented and nothing stale survives a cycle without it.
        state.direction = _finite(payload.get("direction"))
        state.strength = _finite(payload.get("strength"))

    # ── التِكّة ──────────────────────────────────────────────────────────
    async def on_tick(self, payload: dict[str, Any]) -> None:
        if not self.running or self.context is None:
            return
        scope = self.scope_of(payload)
        if scope is None:
            self.invalid += 1
            return
        bid = _finite(payload.get("bid"))
        ask = _finite(payload.get("ask"))
        price = _finite(payload.get("price", payload.get("last")))
        if price is None and bid is not None and ask is not None:
            price = (bid + ask) / 2.0
        source_ts = _finite(payload.get("source_timestamp",
                                        payload.get("timestamp", payload.get("ts"))))
        if price is None or price <= 0 or source_ts is None or source_ts <= 0:
            self.invalid += 1
            return
        state = self.states.setdefault(scope, SectionLiveState())
        if state.timestamps and source_ts <= state.timestamps[-1]:
            self.invalid += 1
            return
        # ١) تحديث الأدلّة الداخلية من التِكّة نفسها.
        if state.prices:
            state.returns.append((price - state.prices[-1]) / state.prices[-1])
        state.prices.append(price)
        state.spreads.append(max(0.0, (ask - bid) / price)
                             if bid is not None and ask is not None else 0.0)
        state.timestamps.append(source_ts)
        state.sequence += 1
        state.last_tick_ts = source_ts
        self.ticks += 1
        # ٢) إعادة الحساب والنشر — بلا انتظار إغلاق شمعة.
        await self._publish(scope, state, source_ts)

    async def on_second(self, payload: dict[str, Any]) -> None:
        """§١٤ — تجاوز الـTTL يجعل النتيجة `STALE` ولو لم يقع خطأ."""
        if not self.running or self.context is None:
            return
        pulse = payload.get("official_time") if isinstance(payload, dict) else None
        now = _finite(pulse)
        now = clock.now() if now is None else now
        for scope, state in list(self.states.items()):
            if not state.sequence or not state.last_tick_ts:
                continue
            if now - state.last_tick_ts > self.ttl_now():
                await self._publish(scope, state, state.last_tick_ts,
                                    forced_state=STATE_STALE, now=now)
                self.stale_published += 1

    # ── الحساب ───────────────────────────────────────────────────────────
    def evidence(self, state: SectionLiveState) -> dict[str, float]:
        """أدلّة القسم من التِكّات — كلّها مقيسة، ولا واحدة منها زمن ثابت."""
        returns = list(state.returns)
        recent = returns[-12:]
        # تغطية العيّنة: كم تِكّة صالحة تراكمت (ليست مؤقّتًا ولا `sleep`).
        sample = _clip(len(returns) / 24.0 * 100.0)
        # ⛔ التغطية وحدها تجعل العمق عدّاد تِكّات — وهو ما يمنعه §٦.
        #    فلا بدّ من دليل **مادّة**: كم حركةً فعليّة حملت هذه التِكّات.
        #    ثلاثون تِكّة على سوق ساكن ليست عمقًا كثلاثين على سوق يتحرّك.
        movement = _clip(sum(abs(value) for value in recent) * 160_000.0)
        # اكتمال وحدات القسم — سياق الشمعة يدخل العمق ولا يصنعه وحده.
        units_ok = sum(1 for unit in state.units.values()
                       if isinstance(unit, dict) and unit.get("status") == "ok")
        units = (_clip(units_ok / state.expected_units * 100.0)
                 if state.expected_units > 0 else 0.0)
        # استقرار: كم الحركة نظيفة من الضجيج.
        if recent:
            mean = sum(recent) / len(recent)
            variance = sum((value - mean) ** 2 for value in recent) / len(recent)
            mean_abs = sum(abs(value) for value in recent) / len(recent)
            noise_ratio = math.sqrt(variance) / max(mean_abs, 1e-9)
        else:
            noise_ratio = 0.0
        stability = _clip(100.0 - noise_ratio * 60.0)
        average_spread = (sum(state.spreads) / len(state.spreads)
                          if state.spreads else 0.0)
        spread = _clip(100.0 - average_spread * 200_000.0)
        # انتظام وصول التِكّات — انقطاع يعني دليلًا مثقوبًا.
        if len(state.timestamps) > 2:
            gaps = [b - a for a, b in zip(state.timestamps,
                                          list(state.timestamps)[1:])]
            positive = [gap for gap in gaps if gap > 0]
            mean_gap = sum(positive) / len(positive) if positive else 0.0
            continuity = _clip(100.0 - max(positive, default=0.0) /
                               max(mean_gap, 0.001) * 15.0)
        else:
            continuity = 0.0
        # §٥ — اتّساق زمنيّ: هل يقول نصفا النافذة الشيء نفسه؟ دليلٌ يتقلّب
        #      بين نصف وآخر ليس ناضجًا مهما كثرت تِكّاته.
        if len(recent) >= 4:
            half = len(recent) // 2
            early, late = sum(recent[:half]), sum(recent[half:])
            total = abs(early) + abs(late)
            agreement = (_clip(100.0 * (1.0 - abs(early - late) / total))
                         if total > 0 else 0.0)
        else:
            agreement = 0.0
        return {"sample": sample, "movement": movement, "units": units,
                "stability": stability, "spread": spread,
                "continuity": continuity, "agreement": agreement}

    def card(self, scope: tuple[str, str, str], state: SectionLiveState,
             source_ts: float, *, forced_state: str | None = None,
             now: float | None = None) -> dict[str, Any]:
        account, broker, symbol = scope
        parts = self.evidence(state)
        # §٦ — العمق: أدلّة **الكفاية** وحدها. «كم جمعنا قبل السماح بالكلام».
        current_depth = _clip(0.30 * parts["sample"] + 0.30 * parts["movement"] +
                              0.25 * parts["units"] + 0.15 * parts["continuity"])
        # §٥ · §١٠ — الثقة: نضج الأدلّة واتّساقها. ⛔ لا `sample` (وهو
        # `data_completeness` بعينه) ولا أيّ مكوّن يشارك فيه العمق أعلاه.
        confidence = _clip(0.40 * parts["stability"] + 0.30 * parts["spread"] +
                           0.30 * parts["agreement"])
        # §١٢ — العيار من معايرة هذا النطاق، لا من ثابت عالميّ.
        required_depth = self.required_depth_for(scope)
        moment = clock.now() if now is None else now
        age = max(0.0, moment - source_ts)
        fresh = age <= self.ttl_now()
        # §٢١ — حالة الذرّات الإلزامية تحكم القسم قبل أيّ حساب آخر.
        units_state, offenders = required_state(self.section_id, state.units)
        # (٢٠٢٦-٠٨-٢٥) رأي القسم من وحداته — لا من جودة التِكّة وحدها.
        opinion = unit_opinion(state.units)
        direction = (state.direction if state.direction is not None
                     else opinion["direction"])
        strength = (state.strength if state.strength is not None
                    else opinion["strength"])
        if opinion["confidence"] is not None:
            confidence = _clip(opinion["confidence"])
            confidence_source = "units"
        else:
            confidence_source = "tick_quality"
        if forced_state is not None:
            state_name = forced_state
        elif not (account and broker and symbol):
            state_name = STATE_INVALID
        elif not fresh:
            state_name = STATE_STALE
        elif units_state and units_state != STATE_READY:
            state_name = units_state
        elif current_depth < required_depth:
            state_name = STATE_ANALYZING
        elif confidence <= 0.0:
            state_name = STATE_NOT_READY
        else:
            state_name = STATE_READY
        card = {
            "account_id": account, "broker": broker, "symbol": symbol,
            "asset": symbol, "section_id": self.section_id,
            "id": "section_%s_live" % self.section_id,
            "analysis_mode": "live_tick", "timeframe": "tick",
            "cycle_id": state.cycle_id or None,
            "source_cycle_timeframe": state.timeframe,
            "current_depth": round(current_depth, 4),
            "required_depth": round(required_depth, 4),
            # §٩ — وزن القسم من معايرة هذا النطاق وحده.
            "weight": _clip(_finite(self.calibration(scope).get("weight")) or 0.0),
            "settings_revision": int(self.calibration(scope).get("revision", 0)),
            "confidence": round(confidence, 4),
            "confidence_source": confidence_source,
            "units_measured": opinion["measured_units"],
            # (٢٠٢٦-٠٨-٢٥) الجاهزية نسبة متدرّجة لا قفزة — الحالة تبقى للتوافق.
            "readiness_pct": (round(min(100.0, current_depth / required_depth
                                        * 100.0), 1)
                              if required_depth > 0 else None),
            "data_completeness": round(parts["sample"], 4),
            "state": state_name, "status": "ok" if state_name == STATE_READY
            else "not_ready", "ready": state_name == STATE_READY,
            # §٢٠ — الإلزاميّات وحالتها ومَن يعطّلها: معلَنة لا مستنتَجة.
            "required_units": sorted(REQUIRED_UNITS.get(self.section_id, ())),
            "required_units_state": units_state or "UNDECLARED",
            "blocking_units": offenders,
            "missing_reason": ("" if state_name == STATE_READY else
                               "REQUIRED_UNITS" if offenders else
                               "STALE" if not fresh else
                               "DEPTH" if current_depth < required_depth else
                               "IDENTITY" if not (account and broker and symbol)
                               else "CONFIDENCE"),
            "sequence": state.sequence, "source_timestamp": source_ts,
            "timestamp": moment, "freshness_age_s": round(age, 4),
            "evidence": {name: round(value, 3) for name, value in parts.items()},
        }
        # NQ seal item 22 (A8): deliver the directional values only when
        # actually measured -- by the observed cycle's own top level, or now
        # (2026-08-25) by the weighted opinion of the section's units. An
        # absent field stays absent so the unified contract declares it.
        if direction is not None:
            card["direction"] = direction
        if strength is not None:
            card["strength"] = strength
        return card

    async def _publish(self, scope: tuple[str, str, str], state: SectionLiveState,
                       source_ts: float, *, forced_state: str | None = None,
                       now: float | None = None) -> None:
        await self.context.publish(self.event_out, self.card(
            scope, state, source_ts, forced_state=forced_state, now=now))
        self.published += 1

    def health_details(self) -> dict[str, Any]:
        return {"live_scopes": len(self.states), "live_ticks": self.ticks,
                "live_published": self.published, "live_invalid": self.invalid,
                "live_stale": self.stale_published,
                "contract": "section_tick_live_v1"}


def section_live(section_id: str, event_out: str) -> Callable[[type], type]:
    """مزيّن قليل التدخّل — لا يمسّ منطق القسم ولا يكسر مستهلكيه.

    مسار الشمعة يبقى كما هو تمامًا؛ يُضاف إليه مسار التِكّة.
    """
    def decorate(cls: type) -> type:
        old_init = cls.__init__
        old_initialize = cls.initialize
        old_start = cls.start
        old_stop = cls.stop
        old_shutdown = cls.shutdown
        old_health = cls.health_check

        def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
            old_init(self, *args, **kwargs)
            self._live_section = SectionLiveKernel(section_id, event_out)

        async def new_initialize(self: Any, context: Any) -> None:
            await old_initialize(self, context)
            await self._live_section.initialize(context)

        async def new_start(self: Any) -> None:
            await old_start(self)
            self._live_section.start()

        async def new_stop(self: Any) -> None:
            self._live_section.stop()
            await old_stop(self)

        async def new_shutdown(self: Any) -> None:
            self._live_section.stop()
            await old_shutdown(self)

        async def new_health(self: Any) -> Any:
            status = await old_health(self)
            try:
                details = dict(status.details or {})
                details.update(self._live_section.health_details())
                return type(status)(state=status.state, message=status.message,
                                    details=details)
            except Exception:
                return status

        cls.__init__ = new_init
        cls.initialize = new_initialize
        cls.start = new_start
        cls.stop = new_stop
        cls.shutdown = new_shutdown
        cls.health_check = new_health
        cls.SECTION_LIVE = True
        return cls
    return decorate
