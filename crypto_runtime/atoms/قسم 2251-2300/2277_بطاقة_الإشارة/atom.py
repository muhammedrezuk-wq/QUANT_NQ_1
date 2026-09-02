from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.3.0"
EVENT_IN_SIZED = "crypto.decision.sized_entry.state"
EVENT_IN_UNIVERSE = "crypto.universe.snapshot.state"
EVENT_IN_WALLS = "sense.walls.state"
EVENT_IN_PROFILE = "sense.volume_profile.state"
EVENT_IN_PRIOR = "sense.prior_day.state"
EVENT_OUT = "crypto.decision.signal_card.state"

_CANDLE_S = 300.0   # إطار القرار 5د — راجع 02-rules.md §2 "5د: إطار القرار الوحيد"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """بطاقة الإشارة v3.1 — المرساة + سلّم الدخول + الوقف + الهدفان + الإلغاء.

    تطبيقٌ حرفيّ لـ`scalping/03-protocol.md` §٣ (الصيغة الملزمة النهائية،
    قرار صاحب المشروع 2026-08-25) و`02-rules.md` §٥/§٧. **كل رقمٍ هنا إمّا
    منسوخٌ حرفيًّا من الصيغة المعمَّمة الموثَّقة، أو مُعلَنٌ صراحةً كتبسيطٍ
    هندسيّ عن الصيغة — راجع الحدود بالشرح لكل تبسيطٍ وسببه.**

    لا تحسب حجمًا ولا رافعة ولا رأس مال — «الكمية دائماً قرار المستخدم»
    حرفيًّا من نص البروتوكول. تنشر أرقام أسعارٍ فقط."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        # `03-protocol.md` §٣: "النطاق حولها ~5-8 ن.أ من السعر" — نقطة وسط النطاق المُعلَن، قابلة للمعايرة.
        self._entry_zone_bps = 6.5
        self._min_spread_multiple = 2.0
        # `02-rules.md` §٧: "خلف المستوى بـ25-33 ن.أ (p90-p95)" — نقطة وسط النطاق المُعلَن.
        self._stop_margin_bps = 29.0
        # `02-rules.md` §٧: "② 6 شموع ✓" · "① لمس-VWAP 10-12 شمعة"
        self._time_stop_class1_candles = 11
        self._time_stop_class2_candles = 6
        self._max_age_s = 90.0
        # عمر المدخل بوابةً — منفصل عن `max_age_s` الذي يبقى شارة صحّة.
        # فصلُهما مقصود: الشارة تصف حالة الذرّة، والبوّابة تحكم على مدخلٍ
        # بعينه. خلطهما يجعل تشديد أحدهما يكذب على الآخر.
        self._input_max_age_s = 30.0
        self._rejected = {"stale_input": 0, "price_beyond_stop": 0}
        self._tick: dict[str, float] = {}          # symbol -> price_tick_size
        self._spread_price: dict[str, float] = {}  # symbol -> spread بوحدة السعر
        self._walls: dict[str, dict[str, Any]] = {}
        self._profile: dict[str, dict[str, Any]] = {}
        self._prior: dict[str, dict[str, Any]] = {}
        self._published = 0
        self._skipped = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        c = context.config
        self._entry_zone_bps = float(c.get("entry_zone_bps", 6.5))
        self._min_spread_multiple = float(c.get("min_spread_multiple", 2.0))
        self._stop_margin_bps = float(c.get("stop_margin_bps", 29.0))
        self._time_stop_class1_candles = int(c.get("time_stop_class1_candles", 11))
        self._time_stop_class2_candles = int(c.get("time_stop_class2_candles", 6))
        self._max_age_s = float(c.get("max_age_s", 90.0))
        self._input_max_age_s = float(c.get("input_max_age_s", 30.0))
        context.subscribe(EVENT_IN_SIZED, self._on_sized)
        context.subscribe(EVENT_IN_UNIVERSE, self._on_universe)
        context.subscribe(EVENT_IN_WALLS, self._on_walls)
        context.subscribe(EVENT_IN_PROFILE, self._on_profile)
        context.subscribe(EVENT_IN_PRIOR, self._on_prior)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # ————— اشتراكات التخزين المؤقّت —————

    async def _on_universe(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        for ring_key in ("core", "outer"):
            rows = payload.get(ring_key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "")
                if not symbol:
                    continue
                tick = _f(row.get("price_tick_size"))
                spread_ticks = _f(row.get("spread_ticks"))
                if tick is not None:
                    self._tick[symbol] = tick
                if tick is not None and spread_ticks is not None:
                    self._spread_price[symbol] = spread_ticks * tick

    async def _on_walls(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._walls[symbol] = payload

    async def _on_profile(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._profile[symbol] = payload

    async def _on_prior(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            self._prior[symbol] = payload

    # ————— الحساب —————

    def _nearest_wall(self, symbol: str, direction: str, avg_entry: float) -> tuple[float | None, str]:
        walls = self._walls.get(symbol) or {}
        side = walls.get("ask_walls") if direction == "long" else walls.get("bid_walls")
        if not isinstance(side, list):
            return None, ""
        candidates = []
        for row in side:
            if not (isinstance(row, list) and len(row) >= 1):
                continue
            price = _f(row[0])
            if price is None:
                continue
            if direction == "long" and price > avg_entry:
                candidates.append(price)
            elif direction == "short" and price < avg_entry:
                candidates.append(price)
        if not candidates:
            return None, ""
        nearest = min(candidates) if direction == "long" else max(candidates)
        return nearest, "wall"

    def _known_levels(self, symbol: str) -> list[tuple[str, float]]:
        levels: list[tuple[str, float]] = []
        profile = self._profile.get(symbol) or {}
        for name in ("poc", "vah", "val"):
            v = _f(profile.get(name))
            if v is not None:
                levels.append((name, v))
        prior = self._prior.get(symbol) or {}
        for name in ("pdh", "pdl"):
            v = _f(prior.get(name))
            if v is not None:
                levels.append((name, v))
        return levels

    def _target2(self, symbol: str, direction: str, ref_price: float) -> tuple[float, str]:
        """أقرب مستوًى معروفٍ أبعد من `ref_price` بذات الاتجاه — تُستدعى مرّتين
        بـv1.2.0: مرّةً للهدف٢ (أبعد من الهدف١)، ومرّةً للراكض (أبعد من الهدف٢)."""
        levels = self._known_levels(symbol)
        beyond = [(name, v) for name, v in levels
                  if (v > ref_price if direction == "long" else v < ref_price)]
        if beyond:
            name, v = min(beyond, key=lambda nv: abs(nv[1] - ref_price))
            return v, "next_level:%s" % name
        return ref_price, "fallback_no_level"   # يُستبدَل بمضاعِف R في _build_card

    def _build_card(self, symbol: str, direction: str, anchor: float, price: float, grade: str | None) -> dict[str, Any]:
        spread_price = self._spread_price.get(symbol)
        width = max(price * self._entry_zone_bps / 1e4,
                     (2.0 * self._min_spread_multiple * (spread_price or 0.0)) / 2.0) if spread_price else \
                price * self._entry_zone_bps / 1e4
        # `03-protocol.md`: "لا يقل عن ضعف سبريد العملة" — طابقٌ على كامل عرض النطاق لا نصفه.
        if spread_price:
            width = max(width, self._min_spread_multiple * spread_price)
        leg_high = anchor + width / 2.0
        leg_low = anchor - width / 2.0
        avg_entry = anchor   # تبسيطٌ مُعلَن: السلّم متناظرٌ حول المرساة، فمتوسّطه = المرساة تمامًا.

        stop_delta = anchor * self._stop_margin_bps / 1e4
        stop = avg_entry - stop_delta if direction == "long" else avg_entry + stop_delta
        r = abs(avg_entry - stop)

        # رتبة الهرم ألف/باء (strategy-full.txt §٤ "الخطوة 4" حرفيًّا): "رتبة
        # ألف: أهداف كاملة + راكض مسموح" مقابل "رتبة باء: أهداف محافظة تسبق
        # الجدران + جني أسرع + لا راكض" — "ضابط رتبة لا بوابة منع" (لا رفض
        # هنا، فقط تبديل الأهداف). رتبةٌ مجهولة (لا 2159 وصلت بعد) تُعامَل
        # معاملة باء الأكثر تحفّظًا — فشلٌ آمنٌ، لا الأوسع. **مضاعِفا 0.75R/1.5R
        # لباء اصطلاحٌ لهذه الذرّة تحديدًا ("جني أسرع" حرفيّ، الرقم غير محدَّد
        # بالوثيقة) — وكذا 3R للراكض ("مسموح" حرفيّ، لا مضاعِف محدَّد.**
        is_grade_a = grade == "A"
        t1_mult = 1.0 if is_grade_a else 0.75
        t2_fallback_mult = 2.0 if is_grade_a else 1.5

        target1_by_r = avg_entry + r * t1_mult if direction == "long" else avg_entry - r * t1_mult
        wall_price, wall_src = self._nearest_wall(symbol, direction, avg_entry)
        r_label = "1R" if is_grade_a else "0.75R"
        if wall_price is not None:
            dist_r = abs(target1_by_r - avg_entry)
            dist_wall = abs(wall_price - avg_entry)
            if dist_wall < dist_r:
                target1, target1_src = wall_price, wall_src
            else:
                target1, target1_src = target1_by_r, r_label
        else:
            target1, target1_src = target1_by_r, r_label

        target2, target2_src = self._target2(symbol, direction, target1)
        if target2_src == "fallback_no_level":
            target2 = avg_entry + t2_fallback_mult * r if direction == "long" else avg_entry - t2_fallback_mult * r
            target2_src = "fallback_2R" if is_grade_a else "fallback_1.5R"

        runner: float | None = None
        runner_src: str | None = None
        if is_grade_a:
            runner, runner_src = self._target2(symbol, direction, target2)
            if runner_src == "fallback_no_level":
                # **إصلاح علّةٍ حقيقية اكتُشفت بالتشغيل الحيّ (v1.2.1):** كانت
                # الاحتياطية `avg_entry ± 3R` — مضاعِفٌ ثابتٌ من الدخول، لا من
                # الهدف٢ الفعليّ. الهدف٢ حين يأتي من مستوًى حقيقيّ (`next_level`)
                # قد يقع بالفعل أبعد من 3R (شوهد فعليًّا: PUMPFUN_USDT هدف٢
                # عند ~3.47R، فصار "الراكض" الثابت عند 3R **أقرب من الهدف٢**
                # — عكس المعنى تمامًا). الاحتياطية الآن نسبيّةٌ للهدف٢ نفسه
                # (+1R دومًا بعده) — تضمن الترتيب الصحيح آليًّا بصرف النظر عن
                # مصدر الهدف٢.
                runner = target2 + r if direction == "long" else target2 - r
                runner_src = "fallback_target2_plus_1R"

        return {
            "anchor": round(anchor, 8), "entry_price": round(avg_entry, 8),
            "entry_leg_high": round(leg_high, 8), "entry_leg_low": round(leg_low, 8),
            "stop_loss": round(stop, 8), "stop_pct": round(r / avg_entry * 100.0, 4),
            "take_profit": round(target1, 8), "take_profit_source": target1_src,
            "take_profit_2": round(target2, 8), "take_profit_2_source": target2_src,
            "take_profit_runner": round(runner, 8) if runner is not None else None,
            "take_profit_runner_source": runner_src,
            "grade_target_profile": "A_full_plus_runner" if is_grade_a else "B_conservative_faster_no_runner",
            "cancel_level": round(stop, 8),   # تبسيطٌ مُعلَن — راجع الحدود بالشرح
            "r_multiple_price": round(r, 8),
        }

    async def _on_sized(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = time.time()
        self._last_at = now
        if not payload.get("approved"):
            self._skipped += 1
            return
        symbol = str(payload.get("symbol") or "")
        direction = str(payload.get("direction") or "")
        price = _f(payload.get("price"))
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        anchor = _f(evidence.get("level_value")) if evidence else None
        if anchor is None:
            anchor = price
        if not symbol or direction not in ("long", "short") or anchor is None:
            self._skipped += 1
            return

        # ── بوّابة العمر — فعليّة لا شارة ────────────────────────────────
        source_at = _f(payload.get("timestamp"))
        if source_at is not None and (now - source_at) > self._input_max_age_s:
            self._rejected["stale_input"] += 1
            self._skipped += 1
            return

        grade = payload.get("grade")
        card = self._build_card(symbol, direction, anchor, price if price is not None else anchor,
                                 str(grade) if grade else None)

        # ── بوّابة المرساة — السعر تجاوز وقفه قبل أن تصدر البطاقة ────────
        stop = _f(card.get("stop_loss"))
        if price is not None and stop is not None:
            breached = (price <= stop) if direction == "long" else (price >= stop)
            if breached:
                self._rejected["price_beyond_stop"] += 1
                self._skipped += 1
                if self._context is not None:
                    self._context.logger.warning(
                        "277 %s %s: السعر %.8f تجاوز الوقف %.8f (مرساة %.8f) — بطاقة مرفوضة",
                        symbol, direction, price, stop, anchor)
                return
        entry_class = payload.get("entry_class")
        time_stop_candles = (self._time_stop_class1_candles if entry_class == "①rejection_at_level"
                              else self._time_stop_class2_candles)

        self._published += 1
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "direction": direction, "entry_class": entry_class,
            "grade": payload.get("grade"), "ring": payload.get("ring"),
            "gate_margin": payload.get("gate_margin"),
            "news_fresh": payload.get("news_fresh"), "news_age_min": payload.get("news_age_min"),
            "max_risk_usd": payload.get("max_risk_usd"),
            "reference_equity_usd": payload.get("reference_equity_usd"),
            "competing_rank": payload.get("competing_rank"),
            "competing_count": payload.get("competing_count"),
            **card,
            "time_stop_candles": time_stop_candles,
            "time_stop_deadline": now + time_stop_candles * _CANDLE_S,
            "timestamp": now,
        })

    async def health_check(self) -> HealthStatus:
        details = {"published": self._published, "skipped": self._skipped,
                   "rejected": dict(self._rejected),
                   "symbols_with_ticks": len(self._tick), "symbols_with_walls": len(self._walls),
                   "symbols_with_profile": len(self._profile), "symbols_with_prior": len(self._prior),
                   "age_s": (time.time() - self._last_at) if self._last_at else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_SIZED_ENTRY", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="SIZED_ENTRY_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="published=%d skipped=%d" % (self._published, self._skipped),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "published": self._published, "skipped": self._skipped}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._published = int(state.get("published", 0))
            self._skipped = int(state.get("skipped", 0))
