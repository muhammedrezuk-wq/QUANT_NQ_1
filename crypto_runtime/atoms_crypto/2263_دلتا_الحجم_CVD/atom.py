from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN = "market.trade"
EVENT_OUT = "micro.cvd.state"

DIV_NONE = "NONE"
DIV_BEARISH = "BEARISH"       # سعرٌ يعلو والتدفّق لا يؤكّد ⇒ امتصاص علويّ
DIV_BULLISH = "BULLISH"       # سعرٌ يهبط والتدفّق لا يؤكّد ⇒ امتصاص سفليّ


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """دلتا الحجم التراكميّة وتباعدها عن السعر.

    كل صفقة معتدٍ شراء تضيف حجمها، ومعتدٍ بيع تطرحه. الـCVD التراكميّ يقيس
    ضغط الأيدي؛ وحين يتحرّك السعر في اتجاه ولا يؤكّده الـCVD في النافذة =
    **تباعد**: استنزافٌ/امتصاصٌ يسبق الانعكاس غالبًا. حاسّة استنزاف لخدمات
    «الاستعادة بعد الإعدام»."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_s = 300.0
        self._max_age_s = 15.0
        self._cvd: dict[str, float] = {}
        self._window: dict[str, deque] = {}          # (time, signed_vol, price)
        self._trades = 0
        self._last_at: float | None = None
        self._div: dict[str, str] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._window_s = float(context.config.get("window_s", 300.0))
        self._max_age_s = float(context.config.get("max_age_s", 15.0))
        context.subscribe(EVENT_IN, self._on_trade)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        price = _f(payload.get("price"))
        size = _f(payload.get("size"))
        side = str(payload.get("side") or "").upper()
        if not symbol or price is None or size is None or side not in ("BUY", "SELL"):
            return
        signed = size if side == "BUY" else -size
        now = time.time()
        self._cvd[symbol] = self._cvd.get(symbol, 0.0) + signed
        window = self._window.setdefault(symbol, deque())
        window.append((now, signed, price))
        cutoff = now - self._window_s
        while window and window[0][0] < cutoff:
            window.popleft()

        # التباعد: اتجاه السعر مقابل اتجاه التدفّق داخل النافذة.
        first_price = window[0][2]
        price_delta = price - first_price
        window_delta = sum(row[1] for row in window)
        divergence = DIV_NONE
        if price_delta > 0 and window_delta < 0:
            divergence = DIV_BEARISH
        elif price_delta < 0 and window_delta > 0:
            divergence = DIV_BULLISH
        self._div[symbol] = divergence
        self._trades += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, {
            "provider": payload.get("provider"), "symbol": symbol,
            "cvd": round(self._cvd[symbol], 8),
            "window_delta": round(window_delta, 8),
            "window_s": self._window_s, "price": price,
            "price_delta": round(price_delta, 8),
            "divergence": divergence, "samples": len(window), "timestamp": now})

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._cvd), "trades": self._trades,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "cvd": {s: round(v, 4) for s, v in self._cvd.items()},
                   "divergence": dict(self._div)}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_TRADE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="TRADE_FEED_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d trades=%d" % (len(self._cvd), self._trades),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "cvd": dict(self._cvd), "trades": self._trades}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._cvd = {str(k): float(v) for k, v in (state.get("cvd") or {}).items()}
            self._trades = int(state.get("trades", 0))
