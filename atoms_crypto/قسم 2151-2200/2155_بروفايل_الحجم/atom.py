from __future__ import annotations

import math
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.volume_profile.state"
_DAY_S = 86400.0


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _profile(candles: list[tuple[float, float, float]], last: float,
             bin_frac: float, va_pct: float) -> tuple[float, float, float, float] | None:
    """بروفايل حجم الجلسة: يوزّع حجم كل شمعة بالتساوي على صناديق سعرية عرضها
    نسبيّ (‏last×bin_frac ≈ 25 نقطة عند سعر بتكوين)، ثم POC = مركز الصندوق
    الأعلى حجمًا، ومنطقة القيمة توسّعٌ جشعٌ من POC نحو الجار الأثقل حتى ضمّ
    va_pct من حجم الجلسة ⇒ (POC, VAH, VAL, عرض الصندوق)."""
    span = max(last * bin_frac, 1e-06)
    bins: dict[int, float] = {}
    for low, high, vol in candles:
        b0 = int(low // span)
        b1 = int(high // span)
        share = vol / (b1 - b0 + 1)
        for b in range(b0, b1 + 1):
            bins[b] = bins.get(b, 0.0) + share
    if not bins:
        return None
    # POC = الصندوق الأعلى حجمًا؛ عند التعادل يُكسر بالأقرب إلى السعر الحاليّ
    # حتى يقع مركزه داخل مدى التداول لا على حافةٍ خارجه (شمعةٌ عريضة متساوية).
    poc_b = max(bins, key=lambda b: (bins[b], -abs((b + 0.5) * span - last)))
    total = sum(bins.values())
    acc, lo_b, hi_b = bins[poc_b], poc_b, poc_b
    target = va_pct * total
    while acc < target:
        up = bins.get(hi_b + 1, 0.0)
        dn = bins.get(lo_b - 1, 0.0)
        if up >= dn and up > 0:
            hi_b += 1
            acc += up
        elif dn > 0:
            lo_b -= 1
            acc += dn
        else:
            break
    poc = (poc_b + 0.5) * span
    vah = (hi_b + 1) * span
    val = lo_b * span
    return poc, vah, val, span


class Atom(AtomBase):
    """بروفايل حجم الجلسة — POC ومنطقة القيمة VAH/VAL.

    يجمّع الحجم **بالسعر** لا بالزمن: أين قَبِل السوق السعر وتبادَل فيه فعلًا.
    POC = السعر الأكثر تداولًا (مركز الدوران ومغناطيسه)، وVAH/VAL حدّا منطقة
    الـ70٪. داخل القيمة سوقُ دوران (لا صفقة من الوسط)، وخارجها سوقُ رحلة.
    مرساةُ منتصف الليل UTC — يُصفَّر البروفايل مع كل يوم UTC جديد."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._bin_frac = 0.0003
        self._va_pct = 0.70
        self._max_age_s = 600.0
        # per symbol: {"day": int, "candles": [(low, high, vol), ...]}
        self._acc: dict[str, dict[str, Any]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeframe = str(context.config.get("timeframe", "5m"))
        self._bin_frac = float(context.config.get("bin_frac", 0.0003))
        self._va_pct = float(context.config.get("value_area_pct", 0.70))
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
        if not symbol or None in (high, low, close, volume, start):
            return
        if volume <= 0 or high < low or close <= 0:
            return
        day = math.floor(start / _DAY_S)
        acc = self._acc.get(symbol)
        if acc is None or acc["day"] != day:
            acc = {"day": day, "candles": []}
            self._acc[symbol] = acc
        acc["candles"].append((low, high, volume))
        result = _profile(acc["candles"], close, self._bin_frac, self._va_pct)
        if result is None:
            return
        poc, vah, val, span = result
        zone = "above_value" if close > vah else \
               "below_value" if close < val else "inside_value"
        now = time.time()
        state = {"provider": payload.get("provider"), "symbol": symbol,
                 "poc": round(poc, 8), "vah": round(vah, 8), "val": round(val, 8),
                 "price": close, "zone": zone, "bin_width": round(span, 8),
                 "bars": len(acc["candles"]), "session_start": day * _DAY_S,
                 "timestamp": now}
        self._state[symbol] = state
        self._updates += 1
        self._last_at = now
        await self._context.publish(EVENT_OUT, state)

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "poc": {s: v["poc"] for s, v in self._state.items()}}
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
