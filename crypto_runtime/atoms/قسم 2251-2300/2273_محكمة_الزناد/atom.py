from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN_AGGRESSOR = "sense.aggressor.state"     # انقلاب المنفّذين
EVENT_IN_WALLS = "sense.walls.state"             # الجدران أمام/خلف
EVENT_IN_OI = "sense.oi.state"                   # رباعية OI
EVENT_IN_PREMIUM = "sense.premium.state"         # العلاوة
EVENT_IN_LENS = "sense.min1_lens.state"          # العدسة 1د
EVENT_OUT = "crypto.decision.trigger_court.state"

VERDICT_CONFIRM = "CONFIRM"
VERDICT_VETO = "VETO"
VERDICT_ABSTAIN = "ABSTAIN"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """محكمة الزناد — تصويتٌ أغلبيّة-تأكيد/فيتو-صريح عبر خمس حواسّ.

    `scalping/02-rules.md` §٥ "محكمة الزناد لحظة أي تفعيل": "تُقرأ لحظتها:
    انقلاب المنفّذين · الجدران أمام/خلف · رباعية OI · العلاوة · العدسة 1د.
    أغلبية مؤكِّدة ⇒ تنفيذ؛ فيتو صريح (قصّ بحجم عنيف + OI ضدنا) ⇒ إلغاء
    وانتظار." هذه الذرّة تُنشر حكمًا **لكل اتجاهٍ مستقلًّا** (long وshort معًا
    لكل رمز) — لا تعرف أيّ اتجاهٍ رشّحه مُصنِّف الدخول 2274، فتحكم على
    الاثنين وتترك 2274 يختار الجهة المطابقة لمرشَّحه.

    **الفيتو ضيّقٌ بالتصميم — حصرًا اجتماع اثنين معًا:** انقلاب المنفّذين
    (قصٌّ عنيفٌ ضدّ الاتجاه، تهيمن الجهة المعاكسة بحجمٍ وحركة سعرٍ عنيفة)
    **و** رباعية OI ضدّنا (تصفية/عدوانٌ حقيقيّ معاكس) — معًا لا أحدهما وحده،
    حرفيًّا كما وثّق النص. الجدران والعلاوة والعدسة **لا تُصوّت فيتو أبدًا**:
    2265 تُعلن نفسها "شاهدٌ لا قاضٍ" صراحةً في `atom.py`، والعدسة تُعلن نفسها
    "أداة عيون لا أداة قرار" — فيتوهما مخالفٌ لتصميمهما المُعلَن. أغلبية
    التأكيد: أغلبية صريحة من الحواسّ الخمس (٣ فأكثر من ٥ تصوّت تأكيدًا)."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._dominance_th = 0.65      # مطابقٌ لعتبة 2266's الافتراضية نفسها
        self._cut_bps = 15.0           # افتراضيّ كريبتويّ غير موثَّق — "قصّ بحجم عنيف" وصفٌ نوعيّ بلا رقم
        self._walls_ratio_th = 1.3     # افتراضيّ كريبتويّ غير موثَّق
        self._lens_vol_x_th = 1.5      # افتراضيّ كريبتويّ غير موثَّق
        self._max_age_s = 30.0
        # symbol -> {"aggressor":..,"walls":..,"oi":..,"premium":..,"lens":..}
        self._cache: dict[str, dict[str, dict[str, Any]]] = {}
        self._updates = 0
        self._verdicts = {VERDICT_CONFIRM: 0, VERDICT_VETO: 0, VERDICT_ABSTAIN: 0}
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        c = context.config
        self._dominance_th = float(c.get("dominance_threshold", 0.65))
        self._cut_bps = float(c.get("cut_bps", 15.0))
        self._walls_ratio_th = float(c.get("walls_ratio_threshold", 1.3))
        self._lens_vol_x_th = float(c.get("lens_vol_x_threshold", 1.5))
        self._max_age_s = float(c.get("max_age_s", 30.0))
        context.subscribe(EVENT_IN_AGGRESSOR, self._make_handler("aggressor"))
        context.subscribe(EVENT_IN_WALLS, self._make_handler("walls"))
        context.subscribe(EVENT_IN_OI, self._make_handler("oi"))
        context.subscribe(EVENT_IN_PREMIUM, self._make_handler("premium"))
        context.subscribe(EVENT_IN_LENS, self._make_handler("lens"))

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _make_handler(self, sense: str):
        async def handler(payload: dict[str, Any]) -> None:
            await self._on_sense(sense, payload)
        return handler

    async def _on_sense(self, sense: str, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        bucket = self._cache.setdefault(symbol, {})
        bucket[sense] = payload
        now = time.time()
        self._updates += 1
        self._last_at = now
        long_verdict, long_votes = self._evaluate(bucket, "long")
        short_verdict, short_votes = self._evaluate(bucket, "short")
        self._verdicts[long_verdict] = self._verdicts.get(long_verdict, 0) + 1
        self._verdicts[short_verdict] = self._verdicts.get(short_verdict, 0) + 1
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol,
            "long": {"verdict": long_verdict, "votes": long_votes},
            "short": {"verdict": short_verdict, "votes": short_votes},
            "senses_seen": sorted(bucket.keys()), "timestamp": now,
        })

    def _evaluate(self, bucket: dict[str, dict[str, Any]], direction: str) -> tuple[str, dict[str, int]]:
        votes = {
            "aggressor": self._vote_aggressor(bucket.get("aggressor"), direction),
            "walls": self._vote_walls(bucket.get("walls"), direction),
            "oi": self._vote_oi(bucket.get("oi"), direction),
            "premium": self._vote_premium(bucket.get("premium"), direction),
            "lens": self._vote_lens(bucket.get("lens"), direction),
        }
        if votes["aggressor"] == -1 and votes["oi"] == -1:
            return VERDICT_VETO, votes
        if sum(1 for v in votes.values() if v > 0) > len(votes) / 2:
            return VERDICT_CONFIRM, votes
        return VERDICT_ABSTAIN, votes

    def _vote_aggressor(self, a: dict[str, Any] | None, direction: str) -> int:
        if not a:
            return 0
        dominance = _f(a.get("dominance")); buy_ratio = _f(a.get("buy_ratio"))
        move = _f(a.get("price_move_bps"))
        if dominance is None or buy_ratio is None:
            return 0
        favors_long = buy_ratio > 0.5
        with_us = favors_long if direction == "long" else not favors_long
        if with_us and dominance >= self._dominance_th:
            return 1
        if not with_us and dominance >= self._dominance_th and move is not None:
            violent = (-move) >= self._cut_bps if direction == "long" else move >= self._cut_bps
            if violent:
                return -1
        return 0

    def _vote_walls(self, w: dict[str, Any] | None, direction: str) -> int:
        if not w:
            return 0
        ratio = _f(w.get("ratio"))
        if ratio is None:
            return 0
        if direction == "long" and ratio >= self._walls_ratio_th:
            return 1
        if direction == "short" and ratio <= (1.0 / self._walls_ratio_th):
            return 1
        return 0

    def _vote_oi(self, o: dict[str, Any] | None, direction: str) -> int:
        if not o:
            return 0
        quadrant = o.get("quadrant")
        if direction == "long":
            if quadrant == "new_longs":
                return 1
            if quadrant == "long_liquidation":
                return -1
        else:
            if quadrant == "new_shorts":
                return 1
            if quadrant == "new_longs":
                return -1
        return 0

    def _vote_premium(self, p: dict[str, Any] | None, direction: str) -> int:
        if not p:
            return 0
        tier = p.get("tier")
        if direction == "long" and tier == "panic":
            return 1
        if direction == "short" and tier == "hot":
            return 1
        return 0

    def _vote_lens(self, lens: dict[str, Any] | None, direction: str) -> int:
        if not lens:
            return 0
        bars = lens.get("bars") or []
        if not bars:
            return 0
        last = bars[-1]
        d = last.get("dir"); vx = _f(last.get("vol_x")) or 0.0
        if d == "+" and direction == "long" and vx >= self._lens_vol_x_th:
            return 1
        if d == "-" and direction == "short" and vx >= self._lens_vol_x_th:
            return 1
        return 0

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._cache), "updates": self._updates,
                   "verdicts": dict(self._verdicts),
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "senses_per_symbol": {s: sorted(v.keys()) for s, v in self._cache.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_SENSE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="SENSES_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d verdicts=%s" % (
                                len(self._cache), self._updates, dict(self._verdicts)),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
