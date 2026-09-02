from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN = "market.trade"
EVENT_OUT = "micro.price_impact.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """أثر السعر — هشاشة السيولة من الصفقات.

    على نافذةٍ زمنيّة: كم تحرّك السعرُ مقابل صافي التدفّق الموقَّع.
      · Kyle λ = Δالسعر ÷ صافي التدفّق (موقَّع)
      · الهشاشة (Amihud) = |Δالسعر/السعر| ÷ إجمالي الحجم (مقياس رقّة الدفتر)
    هشاشةٌ عالية = حركات عنيفة لكل وحدة حجم = قابليّة شلال. حاسّة سياقٍ
    للمخاطرة (تُخفّض الرتبة/توسّع الوقف) لا إشارة اتجاه."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_s = 60.0
        self._min_flow = 1e-9
        self._max_age_s = 15.0
        self._window: dict[str, deque] = {}          # (time, price, signed_vol)
        self._trades = 0
        self._last_at: float | None = None
        self._fragility: dict[str, float] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._window_s = float(context.config.get("window_s", 60.0))
        self._min_flow = float(context.config.get("min_flow", 1e-9))
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
        if not symbol or price is None or size is None or price <= 0 or side not in ("BUY", "SELL"):
            return
        signed = size if side == "BUY" else -size
        now = time.time()
        window = self._window.setdefault(symbol, deque())
        window.append((now, price, signed))
        cutoff = now - self._window_s
        while window and window[0][0] < cutoff:
            window.popleft()
        if len(window) < 2:
            return

        first_price = window[0][1]
        d_price = price - first_price
        net_flow = sum(row[2] for row in window)
        total_vol = sum(abs(row[2]) for row in window)
        kyle_lambda = (d_price / net_flow) if abs(net_flow) > self._min_flow else None
        fragility = (abs(d_price / first_price) / total_vol) if total_vol > 0 else None
        if fragility is not None:
            self._fragility[symbol] = fragility
        self._trades += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, {
            "provider": payload.get("provider"), "symbol": symbol,
            "kyle_lambda": round(kyle_lambda, 10) if kyle_lambda is not None else None,
            "fragility": round(fragility, 10) if fragility is not None else None,
            "net_flow": round(net_flow, 8), "total_volume": round(total_vol, 8),
            "price": price, "window_s": self._window_s,
            "samples": len(window), "timestamp": now})

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._window), "trades": self._trades,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "fragility": {s: round(v, 8) for s, v in self._fragility.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_TRADE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="TRADE_FEED_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d trades=%d" % (len(self._window), self._trades),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "trades": self._trades}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._trades = int(state.get("trades", 0))
