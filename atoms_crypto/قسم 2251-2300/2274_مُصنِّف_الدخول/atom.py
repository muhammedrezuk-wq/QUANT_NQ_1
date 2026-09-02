from __future__ import annotations

import math
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.8.0"
EVENT_IN_LICENSE = "crypto.decision.license.state"
EVENT_IN_VALUE = "crypto.decision.value_state.state"
EVENT_IN_BREAKS = "crypto.decision.breaks.state"
EVENT_IN_COURT = "crypto.decision.trigger_court.state"
EVENT_IN_HTF = "sense.htf.state"
EVENT_IN_FUEL = "sense.fuel.state"
EVENT_IN_ABNORMAL = "sense.abnormal.state"
EVENT_IN_VOLMA = "sense.volume_ma.state"
EVENT_IN_NEWS = "market_data.news_received"
EVENT_IN_CANDLE = "market.candle"             # v1.6.0 — شمعة الرفض (ذيل/ابتلاع) لصنف①
EVENT_IN_ROUND = "sense.round_numbers.state"   # v1.6.0 — تجمّعٌ معزِّز لصنف① (بند ٨)
EVENT_IN_OI = "sense.oi.state"                 # v1.6.0 — رباعيّات يوم التطهير (بند ١٠)
EVENT_IN_PREMIUM = "sense.premium.state"       # v1.6.0 — تأرجح العلاوة ليوم التطهير (بند ١٠)
EVENT_OUT = "crypto.decision.entry_candidate.state"

_CANDLE_TIMEFRAME = "5m"          # `02-rules.md` §٢: "5د: إطار القرار الوحيد"
_WICK_BODY_MULT = 2.0             # اصطلاح pin-bar/hammer قياسيّ — لا رقمٌ حرفيّ بـ`02-rules.md`
_ROUND_NUMBER_CONFLUENCE_BPS = 15.0   # اصطلاح هذه الذرّة — "التجمّع" موثَّقٌ نوعيًّا لا برقم مسافة

_DAY_S = 86400.0
_SESSION_OPENS_MIN = (7 * 60, 13 * 60 + 30)     # لندن 07:00 · نيويورك 13:30 UTC
_SESSION_FREEZE_MIN = 5

CLASS_1 = "①rejection_at_level"
CLASS_2 = "②break_retest"
CLASS_3 = "③filtered_break"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _session_frozen(now: float) -> bool:
    t = time.gmtime(now)
    minute_of_day = t.tm_hour * 60 + t.tm_min
    return any(abs(minute_of_day - open_min) <= _SESSION_FREEZE_MIN for open_min in _SESSION_OPENS_MIN)


class Atom(AtomBase):
    """مُصنِّف الدخول — يُركّب كل الحكّام والحواسّ في مرشّح دخولٍ واحد (أو رفضٍ صريح السبب).

    تطبيقٌ لخطوات ٣-٤ من مواصفة قرار الاتجاه الملزمة (`scalping/02-rules.md`
    §٢): "السعر عند مستوى درجة أولى موافقٍ للرخصة؟ ⇒ بطاقة تُعرض على محكمة
    الزناد — وإلا: «انتظر»." والاتجاه ثابتٌ دومًا بالرخصة (2270): "لا صفقة
    أبداً ضد الرخصة السارية."

    **توضيحٌ حاسم من قراءة 2159 (`sense.htf.state`) نفسها:** بنية ١٥د/٤س
    **لا تبوّب دخولاً** — 2159 تُعلن صراحةً "ممنوعٌ بنيًا اشتراط إجماع الأطر
    للدخول: القرار للـ5د حصرًا"، وهذا يطابق `02-rules.md`'s "هرم الأطر"
    (٤س/١٥د: "ضابط رتبة لا بوابة منع"). فحكّاما الدخول الفعليّان هنا: الرخصة
    (VWAP) وحالة القيمة (توازن/اتجاه) فقط؛ بنية ١٥د تُستهلَك هنا **رتبةً
    (A/B) فقط** عبر `grade_long`/`grade_short` الجاهزين من 2159 نفسها — لا
    توليفٌ إضافي."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._filtered_break_max_points = 150.0    # `02-rules.md` §٥③ شرط ٣: حرفيّ
        self._purge_oi_pct = -1.5                   # `02-rules.md` §٢ "يوم التطهير": حرفيّ
        self._loud_break_ratio = 3.0                 # §٣ ذيل: "الصاخبة ≥×3 سالبة مقاسًا" — حرفيّ
        self._quiet_break_max = 1.5                  # §٣ ذيل: نطاق فرضية "الكسر الهادئ" ١-١.٥ — حرفيّ
        self._news_fresh_minutes = 30.0              # `02-rules.md` §٨: "خبر <30د ⇒ تُخفَّض الثقة" — حرفيّ
        self._max_age_s = 60.0
        # بوابة عمر المدخل — منفصلة عن `max_age_s` (شارة الصحّة).
        self._input_max_age_s = 120.0
        self._stale_inputs = 0
        self._latest_news_at: float | None = None    # وسمٌ عامٌّ للسوق كله — لا ربط رموزٍ بعد (راجع حدود ٢٦١٥)
        self._license: dict[str, dict[str, Any]] = {}
        self._value: dict[str, dict[str, Any]] = {}
        self._breaks: dict[str, dict[str, dict[str, Any]]] = {}   # symbol -> {"pdh":evt,"pdl":evt}
        self._court: dict[str, dict[str, Any]] = {}
        self._htf: dict[str, dict[str, Any]] = {}
        self._fuel: dict[str, dict[str, Any]] = {}
        self._abnormal: dict[str, dict[str, Any]] = {}
        self._volume_ma: dict[str, dict[str, Any]] = {}
        self._round_numbers: dict[str, dict[str, Any]] = {}          # v1.6.0 بند ٨
        self._candle: dict[str, dict[str, Any]] = {}                 # v1.6.0 بند ٩ — الشمعة المغلقة الحالية
        self._prev_candle: dict[str, dict[str, Any]] = {}            # v1.6.0 بند ٩ — التي قبلها (للابتلاع)
        # v1.6.0 بند ١٠ — رباعيّات OI/تدرّجات العلاوة غير المسطّحة عبر الجلسة الحالية (تُصفَّر يوميًّا)
        self._oi_quadrant_day: dict[str, int] = {}
        self._oi_quadrants: dict[str, list[str]] = {}
        self._premium_tier_day: dict[str, int] = {}
        self._premium_tiers: dict[str, set[str]] = {}
        # إطلاقٌ بالحافّة لا بالمستوى (`strategy-full.txt` §٩: "ينطق مرة ثم
        # يصمت حتى يعاد التسليح") — symbol -> (direction, entry_class, arm_key)
        # آخر مرشّحٍ صدر؛ لا يُعاد الصدور لنفس الثلاثيّة حتى تتغيّر.
        self._last_fired: dict[str, tuple[Any, Any, Any]] = {}
        self._resuppressed = 0
        # يوم التطهير (تقريبٌ مبسّط، لا القياس النوعي الكامل — راجع الشرح):
        # symbol -> {"day": int, "oi0": float}
        self._session_oi: dict[str, dict[str, float]] = {}
        self._updates = 0
        self._emitted = 0
        self._blocked: dict[str, int] = {}
        self._blocked_detail: dict[str, dict[str, Any]] = {}   # v1.6.0 — آخر أدلّة حجبٍ لكل سبب، للشفافية
        self._last_at: float | None = None
        self._wick_body_mult = _WICK_BODY_MULT
        self._round_number_confluence_bps = _ROUND_NUMBER_CONFLUENCE_BPS

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        c = context.config
        self._filtered_break_max_points = float(c.get("filtered_break_max_points", 150.0))
        self._purge_oi_pct = float(c.get("purge_day_oi_pct", -1.5))
        self._loud_break_ratio = float(c.get("loud_break_ratio", 3.0))
        self._quiet_break_max = float(c.get("quiet_break_max", 1.5))
        self._news_fresh_minutes = float(c.get("news_fresh_minutes", 30.0))
        self._max_age_s = float(c.get("max_age_s", 60.0))
        self._input_max_age_s = float(c.get("input_max_age_s", 120.0))
        self._wick_body_mult = float(c.get("wick_body_mult", _WICK_BODY_MULT))
        self._round_number_confluence_bps = float(
            c.get("round_number_confluence_bps", _ROUND_NUMBER_CONFLUENCE_BPS))
        context.subscribe(EVENT_IN_LICENSE, self._on_license)
        context.subscribe(EVENT_IN_VALUE, self._on_value)
        context.subscribe(EVENT_IN_BREAKS, self._on_breaks)
        context.subscribe(EVENT_IN_COURT, self._on_court)
        context.subscribe(EVENT_IN_HTF, self._on_htf)
        context.subscribe(EVENT_IN_FUEL, self._on_fuel)
        context.subscribe(EVENT_IN_ABNORMAL, self._on_abnormal)
        context.subscribe(EVENT_IN_VOLMA, self._on_volume_ma)
        context.subscribe(EVENT_IN_NEWS, self._on_news)
        context.subscribe(EVENT_IN_CANDLE, self._on_candle)
        context.subscribe(EVENT_IN_ROUND, self._on_round_numbers)
        context.subscribe(EVENT_IN_OI, self._on_oi)
        context.subscribe(EVENT_IN_PREMIUM, self._on_premium)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # ————— اشتراكات التخزين المؤقّت —————

    async def _on_license(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._license[symbol] = payload
            await self._evaluate(symbol)

    async def _on_value(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._value[symbol] = payload
            await self._evaluate(symbol)

    async def _on_breaks(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        level = str(payload.get("level") or "")
        if symbol and level:
            self._breaks.setdefault(symbol, {})[level] = payload
            await self._evaluate(symbol)

    async def _on_court(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._court[symbol] = payload
            await self._evaluate(symbol)

    async def _on_htf(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._htf[symbol] = payload

    async def _on_fuel(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        self._fuel[symbol] = payload
        oi = _f(payload.get("oi"))
        ts = _f(payload.get("timestamp"))
        if oi is not None and ts is not None:
            day = math.floor(ts / _DAY_S)
            cached = self._session_oi.get(symbol)
            if cached is None or cached["day"] != day:
                self._session_oi[symbol] = {"day": day, "oi0": oi}

    async def _on_abnormal(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._abnormal[symbol] = payload

    async def _on_volume_ma(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._volume_ma[symbol] = payload

    async def _on_round_numbers(self, payload: dict[str, Any]) -> None:
        """بند ٨ — تجمّعٌ معزِّز لصنف①، لا بوّابة (`05-round-numbers.md`:
        "لا تُتاجَر وحدها — سببٌ مُعزِّز فقط"). `major`/`mid` محسوبتان أصلًا
        من 2154 نسبةً لسعر الشمعة الحاليّ — عند لحظة صنف① السعر ≈ level_value
        (العودة لحافّة القيمة)، فهما تقريبٌ صادقٌ لقرب المستوى من رقمٍ مستدير."""
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._round_numbers[symbol] = payload

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        """بند ٩ — شمعة٥د مغلقةٌ فقط (2621 لا ينشر جارية). تُبقي آخر شمعتين
        فقط لكشف الابتلاع؛ لا حاجة لنافذةٍ أطول."""
        if not self._running or not isinstance(payload, dict):
            return
        if str(payload.get("timeframe")) != _CANDLE_TIMEFRAME:
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        prior = self._candle.get(symbol)
        if prior is not None:
            self._prev_candle[symbol] = prior
        self._candle[symbol] = payload

    async def _on_oi(self, payload: dict[str, Any]) -> None:
        """بند ١٠ — يتبّع رباعيّات ٢١٧٠ غير المسطّحة عبر الجلسة الحالية
        (تُصفَّر يوميًّا، نفس حدّ يوم `_on_fuel`) لفحص "موجاتٌ كلّها قسريّة"
        (`02-rules.md`: تصفية `long_liquidation` أو تغطية `short_covering`
        حصرًا — أي `new_longs`/`new_shorts` يكسر الشرط، `flat` محايدٌ يُهمَل)."""
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        quadrant = payload.get("quadrant")
        ts = _f(payload.get("timestamp"))
        if not symbol or not quadrant or ts is None:
            return
        day = math.floor(ts / _DAY_S)
        if self._oi_quadrant_day.get(symbol) != day:
            self._oi_quadrant_day[symbol] = day
            self._oi_quadrants[symbol] = []
        if quadrant != "flat":
            self._oi_quadrants[symbol].append(str(quadrant))

    async def _on_premium(self, payload: dict[str, Any]) -> None:
        """بند ١٠ — يتبّع طبقات ٢١٧٣ (`tier`) المُشاهَدة عبر الجلسة الحالية
        لفحص "تتأرجح بين طرفَي الازدحام" — يلزم مشاهدة `hot` و`panic` معًا،
        لا الاستقرار بطرفٍ واحد."""
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        tier = payload.get("tier")
        ts = _f(payload.get("timestamp"))
        if not symbol or not tier or ts is None:
            return
        day = math.floor(ts / _DAY_S)
        if self._premium_tier_day.get(symbol) != day:
            self._premium_tier_day[symbol] = day
            self._premium_tiers[symbol] = set()
        self._premium_tiers[symbol].add(str(tier))

    async def _on_news(self, payload: dict[str, Any]) -> None:
        """وسمٌ لا بوّابة — بأمر المستخدم الصريح: «الأخبار فقط وسم والمشروع
        يعمل». لا `blocked(...)` هنا أبدًا؛ فقط يُحدَّث أحدث توقيت خبرٍ معروف،
        يُقرأ لاحقًا بـ`_evaluate` ليُرفَق كحقلٍ على المرشّح، لا ليمنعه."""
        if not self._running or not isinstance(payload, dict):
            return
        stamp = _f(payload.get("published_at")) or _f(payload.get("timestamp"))
        if stamp is not None:
            self._latest_news_at = max(self._latest_news_at or 0.0, stamp)

    # ————— التصنيف —————

    def _purge_day(self, symbol: str) -> tuple[bool, dict[str, Any]]:
        """التشخيص القياسي الكامل (`02-rules.md` "تصنيف «يوم التطهير»" حرفيًّا):
        ثلاثة شروطٍ بـ"+" — OI ينزف تراكميًّا + العلاوة تتأرجح بين طرفَي
        الازدحام + موجاتٌ كلّها قسريّة (تصفية/تغطية) بلا موجة اقتناع. **حتى
        v1.6.0 كانت هذه الدالة تفحص الشرط الأول فقط** — تشديدٌ نحو التشخيص
        الكامل، لا تخفيفًا: النتيجة تُحجَب إن نقص أيّ شرط، فتصير `purge_day`
        أندر لا أكثر (أيام نزيف OI بلا الشرطين الآخرين لم تعد تُحجَب)."""
        cached = self._session_oi.get(symbol)
        fuel = self._fuel.get(symbol)
        if not cached or not fuel:
            return False, {}
        oi = _f(fuel.get("oi"))
        oi0 = cached.get("oi0")
        if oi is None or not oi0:
            return False, {}
        change_pct = (oi - oi0) / oi0 * 100.0
        oi_bleed = change_pct <= self._purge_oi_pct

        quadrants = self._oi_quadrants.get(symbol) or []
        forced_wave_only = bool(quadrants) and all(
            q in ("short_covering", "long_liquidation") for q in quadrants)

        tiers = self._premium_tiers.get(symbol) or set()
        premium_oscillating = "hot" in tiers and "panic" in tiers

        evidence = {
            "oi_change_pct": round(change_pct, 3), "oi_bleed": oi_bleed,
            "forced_wave_only": forced_wave_only, "oi_quadrants_seen": list(quadrants),
            "premium_oscillating": premium_oscillating, "premium_tiers_seen": sorted(tiers),
        }
        return (oi_bleed and forced_wave_only and premium_oscillating), evidence

    def _rejection_candle(self, symbol: str, direction: str) -> tuple[bool, str | None]:
        """بند ٩ — "شمعة رفض (ذيل/ابتلاع)" حرفيًّا من `02-rules.md` §٥①.
        لا صيغة مقاسة لأيٍّ منهما بالوثيقة — اصطلاحان قياسيّان بالتحليل
        الفنّي (pin-bar/hammer وengulfing)، لا رقمٌ حرفيّ من مصدر المستخدم."""
        candle = self._candle.get(symbol)
        if not candle:
            return False, None
        o, h, l, c = (_f(candle.get("open")), _f(candle.get("high")),
                      _f(candle.get("low")), _f(candle.get("close")))
        if None in (o, h, l, c) or h <= l:
            return False, None
        body = abs(c - o)
        if body > 0:
            if direction == "long" and (min(o, c) - l) >= self._wick_body_mult * body:
                return True, "wick"
            if direction == "short" and (h - max(o, c)) >= self._wick_body_mult * body:
                return True, "wick"
        prev = self._prev_candle.get(symbol)
        if prev:
            po, pc = _f(prev.get("open")), _f(prev.get("close"))
            if None not in (po, pc):
                if direction == "long" and c > o and pc < po and o <= pc and c >= po:
                    return True, "engulfing"
                if direction == "short" and c < o and pc > po and o >= pc and c <= po:
                    return True, "engulfing"
        return False, None

    def _round_number_confluence(self, symbol: str, level_value: float | None) -> dict[str, Any] | None:
        """بند ٨ — `05-round-numbers.md`: "قيمتها في التجمّع" — لا بوّابة،
        وسمُ أدلّةٍ فقط يُرفَق على مرشّح صنف① بالفعل."""
        rn = self._round_numbers.get(symbol)
        if not rn or level_value is None:
            return None
        best: dict[str, Any] | None = None
        for tier in ("major", "mid"):
            bracket = rn.get(tier) or {}
            nearest = _f(bracket.get("nearest"))
            if nearest is None or nearest <= 0:
                continue
            dist_bps = abs(level_value - nearest) / nearest * 1e4
            if dist_bps <= self._round_number_confluence_bps and (best is None or dist_bps < best["distance_bps"]):
                best = {"tier": tier, "value": nearest, "distance_bps": round(dist_bps, 2)}
        return best

    def _classify(self, symbol: str, direction: str) -> tuple[str | None, dict[str, Any], Any]:
        """يُرجع (الصنف أو None، تفاصيل الأدلّة، مفتاح تسليحٍ فريد للحدث المُسبِّب)."""
        value = self._value.get(symbol) or {}
        breaks = self._breaks.get(symbol) or {}
        fuel = self._fuel.get(symbol) or {}
        level_key = "pdh" if direction == "long" else "pdl"
        level_evt = breaks.get(level_key) or {}

        # الصنف ①: عودةٌ لحافّة منطقة القيمة (تقريبٌ — راجع حدود الشرح).
        # مفتاح التسليح = `since` (2271 لا يغيّرها إلا عند تحوّلٍ فعليّ جديد).
        # v1.6.0 (بند ٩): الشرط الحرفيّ الكامل هو "حجمٌ يذبل + شمعة رفض"
        # معًا — كان يُشترَط انتقال القيمة وحده حتى الآن. كلاهما الآن لازمان؛
        # غيابهما لا يُسقِط الرمز من التقييم، فقط لا يُصدر صنف① هذه الجولة
        # (يتابع الفحص لصنفي ②/③ أدناه — لا `return` هنا عمدًا).
        expected_transition = "entered_value_from_below" if direction == "long" else "entered_value_from_above"
        if value.get("transition") == expected_transition:
            level_value = value.get("val") if direction == "long" else value.get("vah")
            vol = self._volume_ma.get(symbol) or {}
            volume_fading = vol.get("signal") == "fade"
            rejection, pattern = self._rejection_candle(symbol, direction)
            if volume_fading and rejection:
                evidence: dict[str, Any] = {
                    "value_transition": value.get("transition"), "level_value": level_value,
                    "volume_signal": vol.get("signal"), "volume_ratio": vol.get("ratio"),
                    "rejection_pattern": pattern,
                }
                confluence = self._round_number_confluence(symbol, level_value)
                if confluence:
                    evidence["round_number_confluence"] = confluence
                return (CLASS_1, evidence, value.get("since"))

        # الصنف ②: كسرٌ صمد بعد إعادة اختبار (2272 confirmed).
        # مفتاح التسليح = طابع زمن حدث 2272 نفسه (يتغيّر فقط عند تحوّل حالةٍ جديد).
        if level_evt.get("event") == "confirmed":
            return (CLASS_2, {"level": level_key, "distance_points": level_evt.get("distance_points"),
                              "level_value": level_evt.get("level_value")},
                    level_evt.get("timestamp"))

        # الصنف ③: كسرٌ طازجٌ + وقودٌ يبني بنفس الجهة + مسافةٌ ≤ ١٥٠ن + ليس كسرًا صاخبًا.
        if level_evt.get("event") == "broken":
            # ── عمر المدخل بوابةً ───────────────────────────────────────
            broken_at = _f(level_evt.get("timestamp"))
            if broken_at is not None and (time.time() - broken_at) > self._input_max_age_s:
                self._stale_inputs += 1
                return None, {}, None
            distance = _f(level_evt.get("distance_points"))
            fuel_state = fuel.get("fuel")
            fuel_matches = (fuel_state == "building_decline" and direction == "short") or \
                           (fuel_state == "building_rise" and direction == "long")
            if fuel_matches and distance is not None and abs(distance) <= self._filtered_break_max_points:
                # الشرط المحذوف والمُصحَّح (02-rules.md §٣ ذيل، دراسة an07b):
                # الكسر الصاخب (≥×3 حجم) سالبٌ متسقًا ⇒ إقصاءٌ صريح، لا علمُ حذرٍ
                # فقط. الهادئ (١-١.٥×) فرضيةٌ غير مثبتة بعد (n=43) ⇒ تُعلَم لا تُشترَط.
                vol = self._volume_ma.get(symbol) or {}
                ratio = _f(vol.get("ratio"))
                if ratio is not None and ratio >= self._loud_break_ratio:
                    return None, {}, None
                quiet = ratio is not None and 1.0 <= ratio <= self._quiet_break_max
                return (CLASS_3, {"level": level_key, "distance_points": distance, "fuel": fuel_state,
                                  "volume_ratio": ratio, "quiet_break_hypothesis": quiet,
                                  "level_value": level_evt.get("level_value")},
                        level_evt.get("timestamp"))

        return None, {}, None

    async def _evaluate(self, symbol: str) -> None:
        if self._context is None:
            return
        now = time.time()
        self._updates += 1
        self._last_at = now

        def blocked(reason: str, **extra: Any) -> None:
            self._blocked[reason] = self._blocked.get(reason, 0) + 1
            if extra:
                self._blocked_detail[reason] = extra

        license_state = self._license.get(symbol)
        value_state = self._value.get(symbol)
        court = self._court.get(symbol)
        if not license_state or not value_state or not court:
            return  # لم تكتمل المدخلات الأساسية بعد — لا شيء يُعلَن، لا نقصٌ يُخترَع

        if _session_frozen(now):
            return blocked("session_freeze")
        abnormal = self._abnormal.get(symbol)
        if abnormal and abnormal.get("abnormal"):
            return blocked("abnormal_regime")
        is_purge_day, purge_evidence = self._purge_day(symbol)
        if is_purge_day:
            return blocked("purge_day", **purge_evidence)

        direction = str(license_state.get("license") or "")
        if direction not in ("long", "short"):
            return blocked("no_license")
        # يوم توازنٍ (داخل القيمة) ⇒ الطرفان عند الحدّين فقط، الوسط ميت —
        # الصنف ① (تحقيقًا في _classify) هو تحديدًا حالة العودة لحافّة القيمة؛
        # داخل القيمة تمامًا بلا عودةٍ حافّية = لا مرشّح، وهذا محقَّقٌ ضمنيًّا
        # لأن _classify يشترط `transition` لا `zone` وحدها.

        entry_class, evidence, arm_key = self._classify(symbol, direction)
        if entry_class is None:
            return blocked("no_setup")

        verdict = ((court.get(direction) or {}).get("verdict"))
        if verdict == "VETO":
            return blocked("trigger_court_veto")
        if verdict != "CONFIRM":
            return blocked("trigger_court_abstain")

        fired_key = (direction, entry_class, arm_key)
        if self._last_fired.get(symbol) == fired_key:
            self._resuppressed += 1
            return blocked("already_fired_same_setup")
        self._last_fired[symbol] = fired_key

        htf = self._htf.get(symbol) or {}
        grade = htf.get("grade_long") if direction == "long" else htf.get("grade_short")

        self._emitted += 1
        price = value_state.get("price") or license_state.get("price")
        news_age_min = ((now - self._latest_news_at) / 60.0) if self._latest_news_at else None
        news_fresh = news_age_min is not None and news_age_min <= self._news_fresh_minutes
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "direction": direction, "entry_class": entry_class,
            "evidence": evidence, "grade": grade, "price": price,
            "license_since": license_state.get("since"), "value_zone": value_state.get("zone"),
            "trigger_verdict": verdict,
            "news_fresh": news_fresh, "news_age_min": round(news_age_min, 1) if news_age_min is not None else None,
            "timestamp": now,
        })

    async def health_check(self) -> HealthStatus:
        details = {"symbols_seen": len(self._license), "updates": self._updates,
                   "emitted": self._emitted, "resuppressed": self._resuppressed,
                   "blocked": dict(self._blocked), "blocked_detail": dict(self._blocked_detail),
                   "stale_inputs": self._stale_inputs,
                   "input_max_age_s": self._input_max_age_s,
                   "age_s": (time.time() - self._last_at) if self._last_at else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_INPUT", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="INPUTS_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="updates=%d emitted=%d resuppressed=%d" % (
                                self._updates, self._emitted, self._resuppressed),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates, "emitted": self._emitted,
                "resuppressed": self._resuppressed}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
            self._emitted = int(state.get("emitted", 0))
            self._resuppressed = int(state.get("resuppressed", 0))
