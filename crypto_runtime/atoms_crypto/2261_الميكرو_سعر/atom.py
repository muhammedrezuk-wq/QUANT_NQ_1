from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN = "market.depth"
EVENT_OUT = "micro.microprice.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _best(levels: Any) -> tuple[float, float] | None:
    """أفضل مستوى [سعر, حجم] من قائمة عمق مرتّبة تنازليًّا للأفضليّة."""
    if isinstance(levels, list) and levels:
        row = levels[0]
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            price, size = _f(row[0]), _f(row[1])
            if price is not None and size is not None and price > 0 and size >= 0:
                return price, size
    return None


class Atom(AtomBase):
    """السعر العادل الموزون بأحجام الدفتر (Gatheral/Stoikov).

    المنتصف يعامل الجهتين سواءً؛ الميكرو-سعر يميل نحو الجهة **الأقلّ حجمًا**
    (الضغط الأكبر): بيعٌ راقد ضخم أمام السعر يجذب العادل نزولًا قبل أن يتحرّك
    المنتصف. أفقه ثوانٍ — حاسّة زناد لا اتجاه.

        microprice = (bid_p·ask_q + ask_p·bid_q) / (bid_q + ask_q)
    """

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._max_age_s = 10.0
        self._min_size = 0.0
        self._last: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._max_age_s = float(context.config.get("max_age_s", 10.0))
        self._min_size = float(context.config.get("min_size", 0.0))
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
        bid = _best(payload.get("bids"))
        ask = _best(payload.get("asks"))
        if not symbol or bid is None or ask is None:
            return
        bid_p, bid_q = bid
        ask_p, ask_q = ask
        total = bid_q + ask_q
        if total <= self._min_size or ask_p < bid_p:
            return
        micro = (bid_p * ask_q + ask_p * bid_q) / total
        mid = (bid_p + ask_p) / 2.0
        imbalance = (bid_q - ask_q) / total            # +1 ضغط شراء · −1 ضغط بيع
        tilt_bps = (micro - mid) / mid * 1e4 if mid else 0.0
        now = time.time()
        state = {
            "provider": payload.get("provider"), "symbol": symbol,
            "microprice": round(micro, 8), "mid": round(mid, 8),
            "imbalance": round(imbalance, 4), "tilt_bps": round(tilt_bps, 2),
            "best_bid": bid_p, "best_ask": ask_p,
            "bid_size": bid_q, "ask_size": ask_q, "timestamp": now,
        }
        self._last[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._last), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "last": {s: v["tilt_bps"] for s, v in self._last.items()}}
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
