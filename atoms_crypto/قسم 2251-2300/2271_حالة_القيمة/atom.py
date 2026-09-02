from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN = "sense.volume_profile.state"
EVENT_OUT = "crypto.decision.value_state.state"

ROLE = "JUDGE_2_OF_4"          # حكم إطار القيمة (POC/VAH/VAL) — ٢ من ٤

_ENTER_FROM_BELOW = "entered_value_from_below"
_ENTER_FROM_ABOVE = "entered_value_from_above"
_EXIT_UPWARD = "exited_value_upward"
_EXIT_DOWNWARD = "exited_value_downward"


class Atom(AtomBase):
    """حالة القيمة — حكم إطار القيمة (POC/VAH/VAL)، ذرّة ٢ من ٤ حكّام.

    `scalping/02-rules.md` §٢: "VWAP هو الحكَم؛ وPOC/VAH/VAL يحددون إطار
    التوازن". 2155 ينشر `zone` خامًا كل إغلاقة (above_value/inside_value/
    below_value) بالفعل — عمل هذه الذرّة الوحيد: تتبّع *الانتقال* بين
    المناطق، تطبيقًا لآلية الانقلاب (ب) الموثَّقة ("انعكاس دور المستوى: نفس
    الرقم المكسور يلد بطاقة الاتجاه المعاكس من جهته الأخرى"). العودة إلى
    inside_value من خارجها ("عودة" reclaim) والخروج منها كلاهما حدثا مستوى
    درجة أولى (VAL/VAH من عقيدة المستويات §٣).

    **حدٌّ صريح:** لا تكشف نمط شمعة الرفض (ذيل/ابتلاع) ولا ذبول الحجم —
    عنصرا الصنف ① الإضافيان في `scalping/02-rules.md` §٥ — إذ لا حاسّةٌ
    قائمة تنشر نمط الشمعة كحقلٍ مُسمًّى بعد؛ هذا يبقى عمل مُصنِّف الدخول
    2274 إن تيسّر، لا اختراعًا هنا. لا عتبات رقمية في هذه الذرّة."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._max_age_s = 600.0
        # symbol -> {"zone", "since"}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._transitions = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        context.subscribe(EVENT_IN, self._on_profile)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _classify(self, prev_zone: str | None, zone: str) -> str | None:
        if prev_zone is None or prev_zone == zone:
            return None
        if zone == "inside_value" and prev_zone == "below_value":
            return _ENTER_FROM_BELOW
        if zone == "inside_value" and prev_zone == "above_value":
            return _ENTER_FROM_ABOVE
        if zone == "above_value" and prev_zone != "above_value":
            return _EXIT_UPWARD
        if zone == "below_value" and prev_zone != "below_value":
            return _EXIT_DOWNWARD
        return None

    async def _on_profile(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        zone = str(payload.get("zone") or "")
        if not symbol or zone not in ("above_value", "inside_value", "below_value"):
            return
        now = time.time()
        prev = self._state.get(symbol)
        prev_zone = prev["zone"] if prev else None
        changed = prev_zone != zone
        since = now if changed else float(prev["since"]) if prev else now
        transition = self._classify(prev_zone, zone)
        self._state[symbol] = {"zone": zone, "since": since}
        if changed and prev is not None:
            self._transitions += 1
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, {
            "provider": payload.get("provider"), "symbol": symbol, "role": ROLE,
            "zone": zone, "previous_zone": prev_zone, "changed": changed,
            "transition": transition, "since": since, "age_s": round(now - since, 2),
            "poc": payload.get("poc"), "vah": payload.get("vah"), "val": payload.get("val"),
            "price": payload.get("price"), "timestamp": now,
        })

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "transitions": self._transitions,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "zone": {s: v["zone"] for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_PROFILE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="PROFILE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d transitions=%d" % (
                                len(self._state), self._updates, self._transitions),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates, "transitions": self._transitions}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
            self._transitions = int(state.get("transitions", 0))
