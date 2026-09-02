from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.depth"
EVENT_OUT = "sense.walls.state"

ROLE = "WITNESS"                                   # شاهدٌ لا قاضٍ — لا يصنع إشارة ولا يلغي إغلاقًا


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _levels(raw: Any, cap: int) -> list[tuple[float, float]]:
    """أفضل `cap` مستوى [سعر, حجم] من قائمة عمقٍ مرتّبة تنازليًّا للأفضليّة."""
    out: list[tuple[float, float]] = []
    if isinstance(raw, list):
        for row in raw[:cap]:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                price, size = _f(row[0]), _f(row[1])
                if price is not None and size is not None and price > 0 and size >= 0:
                    out.append((price, size))
    return out


class Atom(AtomBase):
    """الجدران — السيولة الراقدة في دفتر الأوامر.

    الدفتر يقول أين اصطفّت النوايا المعلنة قبل أن تُنفَّذ: جدرانٌ ضخمة أمام
    السعر احتكاك، وخلفه سند. تقيس هذه الحاسّة جهتين:
      · النسبة = Σأحجام الطلب ÷ Σأحجام العرض لأعلى المستويات (>1 ثِقل طلب).
      · أكبر 3 جدران لكل جهة (سعر، حجم) — أين تكدّست السيولة فعلًا.
    وتضيف نطاقًا قريبًا حول المنتصف (±near_bps) — ما يُحتمل لمسه أوّلًا.

    شاهدٌ لا قاضٍ: تُقرأ لحظةَ الزناد تأكيدًا/فيتو فقط، ولا تصنع إشارةً ولا
    تلغي إغلاق شمعة. الدفتر قابلٌ للتزييف (سحبُ جدارٍ قبل لمسه)، فلا يُبنى
    عليها وقفٌ ولا دخول — يُستأنس بها وحسب. الحقل `role` يعلن هذا صراحةً."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._levels_cap = 20
        self._top_n = 3
        self._near_bps = 2.5
        self._max_age_s = 10.0
        self._last: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._levels_cap = int(context.config.get("levels", 20))
        self._top_n = int(context.config.get("top_n", 3))
        self._near_bps = float(context.config.get("near_bps", 2.5))
        self._max_age_s = float(context.config.get("max_age_s", 10.0))
        context.subscribe(EVENT_IN, self._on_depth)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_depth(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        bids = _levels(payload.get("bids"), self._levels_cap)
        asks = _levels(payload.get("asks"), self._levels_cap)
        if not symbol or not bids or not asks:
            return
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        if best_ask < best_bid:                       # دفترٌ متقاطع (بيانات فاسدة)
            return
        mid = (best_bid + best_ask) / 2.0
        bid_sum = sum(sz for _, sz in bids)
        ask_sum = sum(sz for _, sz in asks)
        total = bid_sum + ask_sum
        ratio = (bid_sum / ask_sum) if ask_sum > 0 else None
        imbalance = ((bid_sum - ask_sum) / total) if total > 0 else 0.0

        # النطاق القريب (±near_bps) حول المنتصف — سيولةٌ يُرجَّح لمسها قبل غيرها.
        band = mid * self._near_bps / 1e4
        near_bid = sum(sz for pr, sz in bids if mid - pr <= band)
        near_ask = sum(sz for pr, sz in asks if pr - mid <= band)
        near_ratio = (near_bid / near_ask) if near_ask > 0 else None

        # أكبر top_n جدران لكل جهة (بالحجم): [سعر, حجم].
        bid_walls = [[pr, sz] for pr, sz in sorted(bids, key=lambda r: -r[1])[:self._top_n]]
        ask_walls = [[pr, sz] for pr, sz in sorted(asks, key=lambda r: -r[1])[:self._top_n]]

        now = time.time()
        state = {
            "provider": payload.get("provider"), "symbol": symbol, "role": ROLE,
            "mid": round(mid, 8), "levels": min(len(bids), len(asks)),
            "bid_sum": round(bid_sum, 8), "ask_sum": round(ask_sum, 8),
            "ratio": round(ratio, 4) if ratio is not None else None,
            "imbalance": round(imbalance, 4),
            "near_bid_sum": round(near_bid, 8), "near_ask_sum": round(near_ask, 8),
            "near_ratio": round(near_ratio, 4) if near_ratio is not None else None,
            "bid_walls": bid_walls, "ask_walls": ask_walls, "timestamp": now,
        }
        self._last[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._last), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "ratio": {s: v["ratio"] for s, v in self._last.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_DEPTH", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="DEPTH_FEED_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._last), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
