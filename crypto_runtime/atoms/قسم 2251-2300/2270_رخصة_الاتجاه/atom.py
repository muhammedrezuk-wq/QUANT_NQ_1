from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN = "sense.vwap.state"
EVENT_OUT = "crypto.decision.license.state"

ROLE = "JUDGE_1_OF_4"          # حكم VWAP وحده — الحكّام الثلاثة الباقون (إطار
                                # القيمة/بنية 15د/نوع اليوم) يُركَّبون لاحقًا في 2274


class Atom(AtomBase):
    """رخصة الاتجاه — حكم VWAP فقط، ذرّة ١ من ٤ حكّام (`scalping/02-rules.md` §٢).

    2151 ينشر `license` خامًا لكل إغلاقة ٥د (فوق VWAP=long تحته=short) — محسوبٌ
    أصلًا من إغلاقاتٍ لا وخزات (2151 لا يستمع إلا لـmarket.candle)، فقاعدة
    "الوخزات لا تنقل الرخصة" محقَّقةٌ بنيويًّا هناك. عمل هذه الذرّة الوحيد: تتبّع
    *التحوّل* — هل الرخصة تغيّرت عن آخر إغلاقة، ومتى بدأت الحاليّة — تطبيقًا لآلية
    الانقلاب (أ) ولخطوة ٢ من مواصفة قرار الاتجاه ("تغيّرت الرخصة؟ ⇒ تُلغى بطاقات
    الاتجاه الميت..."). لا تصنع الرخصة النهائية وحدها: التركيب الكامل لأربعة
    الحكّام (VWAP + إطار القيمة 2271 + بنية 15د 2159 + نوع اليوم) يحدث في
    مُصنِّف الدخول 2274 — هذه الذرّة تنشر حكم VWAP وحده صراحةً، لا "الرخصة
    النهائية". لا عتبات قابلة للمعايرة هنا (تتبّع حالةٍ صرف، لا فرز رقمي)."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._max_age_s = 600.0
        # symbol -> {"license", "since"}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._flips = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        context.subscribe(EVENT_IN, self._on_vwap)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_vwap(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        license_now = str(payload.get("license") or "")
        if not symbol or license_now not in ("long", "short"):
            return
        now = time.time()
        prev = self._state.get(symbol)
        changed = prev is None or prev["license"] != license_now
        since = now if changed else float(prev["since"]) if prev else now
        self._state[symbol] = {"license": license_now, "since": since}
        if changed and prev is not None:
            self._flips += 1
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, {
            "provider": payload.get("provider"), "symbol": symbol, "role": ROLE,
            "license": license_now,
            "previous_license": prev["license"] if prev else None,
            "changed": changed,
            "since": since, "age_s": round(now - since, 2),
            "vwap": payload.get("vwap"), "price": payload.get("price"),
            "timestamp": now,
        })

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates, "flips": self._flips,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "license": {s: v["license"] for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_VWAP", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="VWAP_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d flips=%d" % (len(self._state), self._updates, self._flips),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates, "flips": self._flips}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
            self._flips = int(state.get("flips", 0))
