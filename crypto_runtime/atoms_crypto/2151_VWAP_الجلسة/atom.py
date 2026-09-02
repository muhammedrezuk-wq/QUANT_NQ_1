from __future__ import annotations

import math
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.vwap.state"
_DAY_S = 86400.0


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """VWAP الجلسة ونطاقاه ±1σ — الحكَم الأوّل للرخصة.

    من شموع الإطار المختار منذ منتصف الليل UTC: متوسطٌ موزون بالحجم لسعر
    hlc3، وانحرافٌ معياريّ موزون. فوق VWAP = رخصة لونغ، تحته = شورت،
    والنطاقان (±1σ) منطقتا تمدّد/قيمة. يُصفَّر مع كل يوم UTC (مرساة الجلسة)."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._band_mult = 1.0
        self._max_age_s = 600.0
        # per symbol: {"day", "sum_v", "sum_tpv", "sum_tp2v", "last"}
        self._acc: dict[str, dict[str, float]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeframe = str(context.config.get("timeframe", "5m"))
        self._band_mult = float(context.config.get("band_mult", 1.0))
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if str(payload.get("timeframe")) != self._timeframe:
            return
        symbol = str(payload.get("symbol") or "")
        high = _f(payload.get("high")); low = _f(payload.get("low"))
        close = _f(payload.get("close")); volume = _f(payload.get("volume"))
        start = _f(payload.get("period_start"))
        if not symbol or None in (high, low, close, volume, start) or volume <= 0:
            return
        day = math.floor(start / _DAY_S)
        acc = self._acc.get(symbol)
        if acc is None or acc["day"] != day:
            acc = {"day": day, "sum_v": 0.0, "sum_tpv": 0.0, "sum_tp2v": 0.0}
            self._acc[symbol] = acc
        tp = (high + low + close) / 3.0
        acc["sum_v"] += volume
        acc["sum_tpv"] += tp * volume
        acc["sum_tp2v"] += tp * tp * volume
        vwap = acc["sum_tpv"] / acc["sum_v"]
        variance = max(0.0, acc["sum_tp2v"] / acc["sum_v"] - vwap * vwap)
        sigma = math.sqrt(variance)
        upper = vwap + self._band_mult * sigma
        lower = vwap - self._band_mult * sigma
        position = "above" if close > upper else "below" if close < lower else \
                   "over" if close > vwap else "under"
        now = time.time()
        state = {"provider": payload.get("provider"), "symbol": symbol,
                 "vwap": round(vwap, 8), "sigma": round(sigma, 8),
                 "upper": round(upper, 8), "lower": round(lower, 8),
                 "price": close, "position": position,
                 "license": "long" if close > vwap else "short",
                 "session_start": day * _DAY_S, "timestamp": now}
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "vwap": {s: v["vwap"] for s, v in self._state.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_CANDLE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CANDLE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d updates=%d" % (len(self._state), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
