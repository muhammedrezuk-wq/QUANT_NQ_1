from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN = "market.depth"
EVENT_OUT = "micro.ofi.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _best(levels: Any) -> tuple[float, float] | None:
    if isinstance(levels, list) and levels:
        row = levels[0]
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            price, size = _f(row[0]), _f(row[1])
            if price is not None and size is not None and price > 0 and size >= 0:
                return price, size
    return None


def _increment(price: float, size: float,
               prev_price: float, prev_size: float, is_bid: bool) -> float:
    """مساهمة OFI من جهة واحدة بين لقطتين (Cont/Kukanov/Stoikov).

    الطلب: سعرٌ أعلى ⇒ +الحجم الجديد · سعرٌ ثابت ⇒ فرق الحجم · أدنى ⇒ −القديم.
    العرض: مرآةٌ لها. والـOFI = مساهمة الطلب − مساهمة العرض."""
    if is_bid:
        if price > prev_price:
            return size
        if price == prev_price:
            return size - prev_size
        return -prev_size
    # ask
    if price > prev_price:
        return -prev_size
    if price == prev_price:
        return size - prev_size
    return size


class Atom(AtomBase):
    """اختلال تدفّق الأوامر — إشارة اتجاهيّة قصيرة من ديناميكا الدفتر.

    الدفتر الثابت يقول «كم» راقد؛ الـOFI يقول «كيف يتحرّك»: كل لقطة عمقٍ
    تُقارن بسابقتها، وتُجمَع مساهماتها في نافذة زمنيّة. موجبٌ = ضغط شراء
    متراكم، سالبٌ = ضغط بيع. أفقه ثوانٍ — للمسجّل والزناد لا للتداول اليدويّ."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_s = 30.0
        self._max_age_s = 10.0
        self._prev: dict[str, tuple[float, float, float, float]] = {}
        self._events: dict[str, deque] = {}
        self._sum: dict[str, float] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._window_s = float(context.config.get("window_s", 30.0))
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
        bid = _best(payload.get("bids"))
        ask = _best(payload.get("asks"))
        if not symbol or bid is None or ask is None:
            return
        bid_p, bid_q = bid
        ask_p, ask_q = ask
        now = time.time()
        prev = self._prev.get(symbol)
        self._prev[symbol] = (bid_p, bid_q, ask_p, ask_q)
        if prev is None:
            return                                    # أوّل لقطة: لا فرق بعد
        pbid_p, pbid_q, pask_p, pask_q = prev
        ofi = (_increment(bid_p, bid_q, pbid_p, pbid_q, True)
               - _increment(ask_p, ask_q, pask_p, pask_q, False))
        events = self._events.setdefault(symbol, deque())
        events.append((now, ofi))
        self._sum[symbol] = self._sum.get(symbol, 0.0) + ofi
        cutoff = now - self._window_s
        while events and events[0][0] < cutoff:
            self._sum[symbol] -= events.popleft()[1]
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, {
            "provider": payload.get("provider"), "symbol": symbol,
            "ofi": round(self._sum[symbol], 8), "window_s": self._window_s,
            "samples": len(events), "instant": round(ofi, 8), "timestamp": now})

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._events), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "ofi": {s: round(v, 4) for s, v in self._sum.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_DEPTH", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="DEPTH_FEED_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._events), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
