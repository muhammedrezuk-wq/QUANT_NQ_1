from __future__ import annotations

import math
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.6.1"
EVENT_IN_CANDIDATE = "crypto.decision.entry_candidate.state"
EVENT_IN_TRADE = "platform.trade_event"      # نتيجة يدوية مؤكدة من لوحة MEXC
EVENT_IN_UNIVERSE = "crypto.universe.snapshot.state"
EVENT_IN_MEDIAN_RANGE = "sense.median_range.state"
EVENT_OUT = "crypto.decision.sized_entry.state"
_RING_RANK = {"core": 0, "outer": 1, None: 2}

_DAY_S = 86400.0
_TRADE_ID_KEEP = 2000
_GRADE_RANK = {"A": 0, "B": 1, None: 2}


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):
    """محرك المخاطر — سقف مخاطرةٍ %، وحدود يوميّة، وسلّم ترتيب المنافسة.

    `scalping/02-rules.md` §٦ "المخاطرة — غير قابلة للتفاوض":
    "المخاطرة القصوى للصفقة: رصيد × 0.5%"، "حدود اليوم: ٣ خسائر متتالية أو
    −2% ⇒ توقف كامل." و`03-protocol.md` (v3.1، 2026-08-25): "بلا دولارات
    إطلاقاً — الحجم بيد المستخدم." **لذلك هذه الذرّة لا تحسب كميّة عقدٍ ولا
    وقفًا** (لا حاسّة تنشر سعر وقفٍ بعد — طبقة الخروج الكاملة مؤجَّلة للطبقة
    ٢) — تحسب **ميزانية المخاطرة بالدولار فقط** (رصيدٌ مرجعيٌّ × ٪٠.٥)،
    تمامًا كتذكير بطاقة v3.1 الثابت: "اجعل خسارتها بحجمك ≤ 0.5% من رأس
    مالك" — المستخدم يستخدمها ليحجم أمره يدويًّا بنفسه على MEXC.

    سلّم ترتيب المنافسة (`07-future.md`: "الرتبة (A قبل B) ← هامش البوابة ←
    جودة الصنف ← طبقة العملة"، ومستند "الخريطة الهندسية" 2026-08-28 §٩.٣:
    "الرتبة ← هامش البوابة الاقتصادية ← جودة الصنف ← النواة قبل الخارجية")
    يُطبَّق على المرشّحين الطازجين معًا (نافذةٌ قصيرة) ليُرتَّبوا لا ليُقصى
    أحدهم — "صفقة واحدة مفتوحة" قرارٌ يتّخذه المستخدم يدويًّا اليوم (راجع
    الحدود: لا تتبّع مركزٍ آليّ بعد).

    **البوابة الاقتصادية (v1.5.0) — حاجزٌ فعليّ بثلاث درجات، لا ترتيبًا فقط
    بعد اليوم:** `02-rules.md` §١ حرفيًّا: "×٣ فأكثر=مرّ، ×2-3=حدّي (مستويات
    الدرجة الأولى فقط)، <×2=لا تداول على الرمز مهما جمُل الإعداد." الهامش =
    `median_bps` (`sense.median_range.state`/2156) ÷ تكلفةٍ مرجعية (أفق=٥د
    فالجذر=١). هامشٌ **مجهول** (لا حاسّة median_range وصلت للرمز بعد) يُحجَب
    لا يُمرَّر — "ما لا يُقاس لا يدخل محرك الإطلاق" (`strategy-full.txt` §١٤
    حرفيًّا)، فشلٌ آمن لا افتراضٌ متساهل. الدرجة الحدّية (×2-3) تستبعد الصنف③
    وحده (كسرٌ مُرشَّحٌ لم يُختبَر — الصنفان ①② كلاهما مبنيّان أصلًا على مستوى
    درجة أولى موثَّق). الهامش يبقى أيضًا معيار ترتيبٍ لمن اجتاز (راجع
    `_rank_key`) — الحجب والترتيب معًا لا أحدهما بدل الآخر."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._reference_equity_usd = 300.0    # نفس المثال المرجعيّ في `02-rules.md` §٦ — يُعدَّل حيًّا
        self._risk_pct = 0.5
        self._daily_loss_pct = 2.0
        self._max_consecutive_losses = 3
        self._competing_window_s = 60.0
        self._cost_bps = 3.0                  # `02-rules.md` §١ "الكلفة الأسوأ ≈ 3 ن.أ": حرفيّ
        # `02-rules.md` §١: "×3 فأكثر=مرّ، ×2-3=حدّي (مستويات الدرجة الأولى فقط)، <×2=لا تداول" — حرفيّ
        self._gate_block_below = 2.0
        self._gate_borderline_below = 3.0
        self._max_age_s = 90.0
        self._gate_blocked: dict[str, int] = {}
        self._median_bps: dict[str, float] = {}
        self._class_quality = {"②break_retest": 0, "①rejection_at_level": 1, "③filtered_break": 2}
        # نافذةٌ قصيرة: symbol -> (candidate_payload, received_at)
        self._recent: dict[str, tuple[dict[str, Any], float]] = {}
        self._core_symbols: set[str] = set()
        self._outer_symbols: set[str] = set()
        # نتائج الإغلاق تصل بإدخال أسمر اليدوي من لوحة MEXC. معرّف الصفقة
        # إلزامي ومحفوظ في اللقطة كي لا يضاعف retry الخسارة أو الربح.
        self._daily: dict[str, Any] = {"day": None, "pnl_usd": 0.0, "consecutive_losses": 0}
        self._processed_trade_ids: list[str] = []
        self._processed_trade_id_set: set[str] = set()
        self._trade_results = 0
        self._duplicate_trade_results = 0
        self._invalid_trade_results = 0
        self._updates = 0
        self._sized = 0
        self._halted_count = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        c = context.config
        self._reference_equity_usd = float(c.get("reference_equity_usd", 300.0))
        self._risk_pct = float(c.get("risk_pct_per_trade", 0.5))
        self._daily_loss_pct = float(c.get("daily_loss_halt_pct", 2.0))
        self._max_consecutive_losses = int(c.get("max_consecutive_losses", 3))
        self._competing_window_s = float(c.get("competing_window_s", 60.0))
        self._cost_bps = float(c.get("cost_bps", 3.0))
        self._gate_block_below = float(c.get("gate_block_below", 2.0))
        self._gate_borderline_below = float(c.get("gate_borderline_below", 3.0))
        self._max_age_s = float(c.get("max_age_s", 90.0))
        context.subscribe(EVENT_IN_CANDIDATE, self._on_candidate)
        context.subscribe(EVENT_IN_TRADE, self._on_trade)
        context.subscribe(EVENT_IN_UNIVERSE, self._on_universe)
        context.subscribe(EVENT_IN_MEDIAN_RANGE, self._on_median_range)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _roll_day(self, now: float) -> None:
        day = math.floor(now / _DAY_S)
        if self._daily["day"] != day:
            self._daily = {"day": day, "pnl_usd": 0.0, "consecutive_losses": 0}

    def _daily_halted(self) -> bool:
        max_loss_usd = self._reference_equity_usd * self._daily_loss_pct / 100.0
        return (self._daily["consecutive_losses"] >= self._max_consecutive_losses
                or self._daily["pnl_usd"] <= -max_loss_usd)

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        """نتيجة إغلاق مؤكدة يدويًا من لوحة MEXC؛ لا تنفيذ آلي هنا.

        ``trade_id`` هو مفتاح المتانة: إعادة إرسال النتيجة نفسها لا تغيّر
        الدفتر مرتين. الربح يصفر سلسلة الخسائر، والخسارة تزيدها، والتعادل لا
        يُسمى خسارة ولا يمس السلسلة.
        """
        if not self._running or not isinstance(payload, dict):
            return
        trade_id = str(payload.get("trade_id") or "").strip()
        pnl = _f(payload.get("pnl_usd"))
        if not trade_id or pnl is None:
            self._invalid_trade_results += 1
            return
        if trade_id in self._processed_trade_id_set:
            self._duplicate_trade_results += 1
            return
        self._processed_trade_ids.append(trade_id)
        self._processed_trade_id_set.add(trade_id)
        while len(self._processed_trade_ids) > _TRADE_ID_KEEP:
            removed = self._processed_trade_ids.pop(0)
            self._processed_trade_id_set.discard(removed)
        now = time.time()
        self._roll_day(now)
        self._daily["pnl_usd"] += pnl
        if pnl > 0:
            self._daily["consecutive_losses"] = 0
        elif pnl < 0:
            self._daily["consecutive_losses"] += 1
        self._trade_results += 1

    async def _on_universe(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        core = payload.get("core")
        outer = payload.get("outer")
        if isinstance(core, list):
            self._core_symbols = {str(row.get("symbol")) for row in core if isinstance(row, dict) and row.get("symbol")}
        if isinstance(outer, list):
            self._outer_symbols = {str(row.get("symbol")) for row in outer if isinstance(row, dict) and row.get("symbol")}

    async def _on_median_range(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        bps = _f(payload.get("median_bps"))
        if symbol and bps is not None:
            self._median_bps[symbol] = bps

    def _ring(self, symbol: str) -> str | None:
        if symbol in self._core_symbols:
            return "core"
        if symbol in self._outer_symbols:
            return "outer"
        return None

    def _gate_margin(self, symbol: str) -> float | None:
        bps = self._median_bps.get(symbol)
        if bps is None or self._cost_bps <= 0:
            return None
        return round(bps / self._cost_bps, 3)

    def _rank_key(self, symbol: str, candidate: dict[str, Any]) -> tuple:
        grade_rank = _GRADE_RANK.get(candidate.get("grade"), 2)
        margin = self._gate_margin(symbol)
        margin_rank = -margin if margin is not None else 0.0   # أعلى هامشٍ أفضل
        class_rank = self._class_quality.get(candidate.get("entry_class"), 9)
        ring_rank = _RING_RANK.get(self._ring(symbol), 2)
        return (grade_rank, margin_rank, class_rank, ring_rank, symbol)

    async def _on_candidate(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        now = time.time()
        self._roll_day(now)
        self._updates += 1
        self._last_at = now

        cutoff = now - self._competing_window_s
        self._recent = {s: (c, t) for s, (c, t) in self._recent.items() if t >= cutoff}
        self._recent[symbol] = (payload, now)

        if self._daily_halted():
            self._halted_count += 1
            await self._context.publish(EVENT_OUT, {
                "symbol": symbol, "approved": False, "reason": "DAILY_RISK_HALTED",
                "daily_pnl_usd": round(self._daily["pnl_usd"], 2),
                "consecutive_losses": self._daily["consecutive_losses"], "timestamp": now,
            })
            return

        # `02-rules.md` §١ — البوّابة الاقتصادية الحادّة (v1.5.0). كانت هامشًا
        # ترتيبيًّا فقط حتى الآن؛ صارت حاجزًا فعليًّا بثلاث درجات كما نصّت الوثيقة
        # حرفيًّا، لا تبسيطًا ثنائيًّا. هامشٌ مجهول (لا `sense.median_range.state`
        # وصل للرمز بعد) يُحجَب لا يُمرَّر — "ما لا يُقاس لا يدخل محرك الإطلاق"
        # (`strategy-full.txt` §١٤ حرفيًّا)، فشلٌ آمنٌ لا افتراضٌ متساهل.
        margin = self._gate_margin(symbol)
        if margin is None:
            self._gate_blocked["ECONOMIC_GATE_UNKNOWN"] = self._gate_blocked.get("ECONOMIC_GATE_UNKNOWN", 0) + 1
            await self._context.publish(EVENT_OUT, {
                "symbol": symbol, "approved": False, "reason": "ECONOMIC_GATE_UNKNOWN", "timestamp": now,
            })
            return
        if margin < self._gate_block_below:
            self._gate_blocked["ECONOMIC_GATE_BLOCKED"] = self._gate_blocked.get("ECONOMIC_GATE_BLOCKED", 0) + 1
            await self._context.publish(EVENT_OUT, {
                "symbol": symbol, "approved": False, "reason": "ECONOMIC_GATE_BLOCKED",
                "gate_margin": margin, "timestamp": now,
            })
            return
        if margin < self._gate_borderline_below and payload.get("entry_class") == "③filtered_break":
            # حدّي (×2-3): "مستويات الدرجة الأولى فقط" — الصنفان ①② مبنيّان أصلًا
            # على مستوى درجة أولى موثَّق (حافّة قيمة / PDH-PDL)؛ ③ كسرٌ مُرشَّح
            # لم يُختبَر بعد، فيُستبعَد وحده بهذه الدرجة.
            self._gate_blocked["ECONOMIC_GATE_BORDERLINE_CLASS3_EXCLUDED"] = self._gate_blocked.get(
                "ECONOMIC_GATE_BORDERLINE_CLASS3_EXCLUDED", 0) + 1
            await self._context.publish(EVENT_OUT, {
                "symbol": symbol, "approved": False, "reason": "ECONOMIC_GATE_BORDERLINE_CLASS3_EXCLUDED",
                "gate_margin": margin, "timestamp": now,
            })
            return

        # أقفال الحلقة الخارجية (`strategy-full.txt` §٤ حرفيًّا: "رتبة ألف كاملة
        # الأركان فقط. رتبة باء مرفوضة في الخارجية... نصف الحجم القياسي").
        # قفلٌ ثالثٌ موثَّق (سبريد الجلسة الحقيقي للخارجية بدل سبريد النواة
        # بالبوّابة الاقتصادية) غير مطبَّقٍ هنا بعد — فجوةٌ أضيق متبقّية،
        # `cost_bps` ثابتٌ للحلقتين حاليًّا.
        ring = self._ring(symbol)
        grade = payload.get("grade")
        if ring == "outer" and grade != "A":
            self._gate_blocked["OUTER_RING_GRADE_B_REJECTED"] = self._gate_blocked.get(
                "OUTER_RING_GRADE_B_REJECTED", 0) + 1
            await self._context.publish(EVENT_OUT, {
                "symbol": symbol, "approved": False, "reason": "OUTER_RING_GRADE_B_REJECTED",
                "ring": ring, "grade": grade, "timestamp": now,
            })
            return

        ranked = sorted(self._recent.items(), key=lambda kv: self._rank_key(kv[0], kv[1][0]))
        competing_rank = next((i for i, (s, _) in enumerate(ranked) if s == symbol), 0)
        max_risk_usd = round(self._reference_equity_usd * self._risk_pct / 100.0, 4)
        if ring == "outer":
            max_risk_usd = round(max_risk_usd / 2.0, 4)   # "نصف الحجم القياسي" — حرفيّ

        self._sized += 1
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "approved": True,
            "direction": payload.get("direction"), "entry_class": payload.get("entry_class"),
            "grade": payload.get("grade"), "ring": self._ring(symbol),
            "gate_margin": margin,
            "price": payload.get("price"), "evidence": payload.get("evidence"),
            "news_fresh": payload.get("news_fresh"), "news_age_min": payload.get("news_age_min"),
            "max_risk_usd": max_risk_usd, "reference_equity_usd": self._reference_equity_usd,
            "risk_pct_per_trade": self._risk_pct,
            "competing_rank": competing_rank, "competing_count": len(self._recent),
            "reason": "sized budget only — no unit quantity: stop-price sizing awaits Tier 2",
            "timestamp": now,
        })

    async def health_check(self) -> HealthStatus:
        details = {"updates": self._updates, "sized": self._sized, "halted": self._halted_count,
                   "gate_blocked": dict(self._gate_blocked),
                   "daily_pnl_usd": round(self._daily["pnl_usd"], 2),
                   "consecutive_losses": self._daily["consecutive_losses"],
                   "trade_results": self._trade_results,
                   "duplicate_trade_results": self._duplicate_trade_results,
                   "invalid_trade_results": self._invalid_trade_results,
                   "recent_candidates": len(self._recent),
                   "age_s": (time.time() - self._last_at) if self._last_at else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(
                state=HealthState.DEGRADED,
                message="AWAITING_FIRST_CANDIDATE trade_results=%d daily_pnl=%+.2f consecutive=%d" % (
                    self._trade_results, self._daily["pnl_usd"],
                    self._daily["consecutive_losses"]),
                details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CANDIDATES_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=("updates=%d sized=%d halted=%d gate_blocked=%d "
                                     "trade_results=%d daily_pnl=%+.2f consecutive=%d") % (
                                self._updates, self._sized, self._halted_count,
                                sum(self._gate_blocked.values()), self._trade_results,
                                self._daily["pnl_usd"], self._daily["consecutive_losses"]),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates, "sized": self._sized,
                "daily": dict(self._daily),
                "processed_trade_ids": list(self._processed_trade_ids),
                "trade_results": self._trade_results,
                "duplicate_trade_results": self._duplicate_trade_results,
                "invalid_trade_results": self._invalid_trade_results}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
            self._sized = int(state.get("sized", 0))
            daily = state.get("daily")
            if isinstance(daily, dict):
                self._daily = daily
            ids = state.get("processed_trade_ids")
            if isinstance(ids, list):
                clean = [str(value) for value in ids if str(value).strip()][-_TRADE_ID_KEEP:]
                self._processed_trade_ids = clean
                self._processed_trade_id_set = set(clean)
            self._trade_results = int(state.get("trade_results", 0))
            self._duplicate_trade_results = int(state.get("duplicate_trade_results", 0))
            self._invalid_trade_results = int(state.get("invalid_trade_results", 0))
