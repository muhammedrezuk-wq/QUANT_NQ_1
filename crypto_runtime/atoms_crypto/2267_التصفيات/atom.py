from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"
EVENT_IN = "feed.binance.liquidation"
EVENT_OUT = "sense.liquidations.state"

ROLE = "WITNESS"                                   # خريطةُ سياقٍ هيكليّة — شاهد لا قاضٍ


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """التصفيات — خريطة كثافة التصفيات القسريّة بمناطق سعريّة.

    **هيكليّة — لا مصدر بعد:** تنتظر جسر بايننس المستقبليّ عبر
    `feed.binance.liquidation` ({symbol, side, price, size, timestamp}).
    صحّتها DEGRADED برسالة `AWAITING_BINANCE_BRIDGE` حتى أوّل حدثٍ فعليّ.

    كل تصفيةٍ تدفّقٌ قسريّ عند سعرٍ ما؛ تراكمها بمناطق (bins بعرض bin_bps)
    يرسم أين تكدّس «الوقود» — المناطق الأكثف مرشّحةٌ لتسارع الحركة عند لمسها.
    تُبقي نافذةً زمنيّة (window_s) فتنسى القديم، وتنشر أكثف top_zones منطقة.

    دلالةُ `side` الخام كما يبثّها الجسر: SELL = تصفيةُ مركزٍ طويل (بيعٌ
    قسريّ) · BUY = تصفيةُ مركزٍ قصير (شراءٌ قسريّ). لا نفرض تأويلًا أبعد؛
    نجمع بالجهة الخام ونترك الحكم لمن يقرأ. شاهدٌ لا قاضٍ (الحقل `role`)."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._bin_bps = 10.0
        self._window_s = 3600.0
        self._top_zones = 5
        self._max_age_s = 600.0
        self._binsize: dict[str, float] = {}         # عرض المنطقة الثابت لكل رمز
        self._events: dict[str, deque] = {}          # (time, zone_key, side, size)
        self._zones: dict[str, dict[int, dict[str, Any]]] = {}
        self._count = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._bin_bps = float(context.config.get("bin_bps", 10.0))
        self._window_s = float(context.config.get("window_s", 3600.0))
        self._top_zones = int(context.config.get("top_zones", 5))
        self._max_age_s = float(context.config.get("max_age_s", 600.0))
        context.subscribe(EVENT_IN, self._on_liquidation)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _prune(self, symbol: str, now: float) -> None:
        """إسقاط الأحداث الأقدم من النافذة، وطرح أثرها من مناطقها."""
        events = self._events.get(symbol)
        zones = self._zones.get(symbol)
        if not events or zones is None:
            return
        cutoff = now - self._window_s
        while events and events[0][0] < cutoff:
            _, zone_key, side, size = events.popleft()
            zone = zones.get(zone_key)
            if zone is None:
                continue
            if side == "BUY":
                zone["buy"] -= size
            else:
                zone["sell"] -= size
            zone["count"] -= 1
            if zone["count"] <= 0:
                zones.pop(zone_key, None)

    async def _on_liquidation(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        price = _f(payload.get("price"))
        size = _f(payload.get("size"))
        side = str(payload.get("side") or "").upper()
        if (not symbol or price is None or size is None or price <= 0
                or size < 0 or side not in ("BUY", "SELL")):
            return
        now = time.time()
        # عرض المنطقة ثابتٌ لكل رمز (من أوّل سعرٍ يُرى) كي لا ينهار الترميز.
        binsize = self._binsize.setdefault(symbol, price * self._bin_bps / 1e4)
        if binsize <= 0:
            return
        zone_key = int(price / binsize)
        zone_price = round((zone_key + 0.5) * binsize, 8)

        zones = self._zones.setdefault(symbol, {})
        zone = zones.get(zone_key)
        if zone is None:
            zone = {"price": zone_price, "buy": 0.0, "sell": 0.0, "count": 0}
            zones[zone_key] = zone
        if side == "BUY":
            zone["buy"] += size
        else:
            zone["sell"] += size
        zone["count"] += 1

        events = self._events.setdefault(symbol, deque())
        events.append((now, zone_key, side, size))
        self._prune(symbol, now)
        self._count += 1
        self._last_at = now

        # أكثف المناطق (بإجمالي الحجم) — payload محدودٌ عمدًا.
        ranked = sorted(zones.values(), key=lambda z: -(z["buy"] + z["sell"]))[:self._top_zones]
        top = [{"price": z["price"], "buy": round(z["buy"], 8), "sell": round(z["sell"], 8),
                "total": round(z["buy"] + z["sell"], 8), "count": z["count"]} for z in ranked]
        buy_total = sum(z["buy"] for z in zones.values())
        sell_total = sum(z["sell"] for z in zones.values())
        await self._context.publish(EVENT_OUT, {
            "provider": payload.get("provider"), "symbol": symbol, "role": ROLE,
            "zones": top, "hottest_zone": (top[0] if top else None),
            "buy_liquidations": round(buy_total, 8), "sell_liquidations": round(sell_total, 8),
            "total_liquidations": round(buy_total + sell_total, 8),
            "zone_count": len(zones), "samples": len(events),
            "bin_bps": self._bin_bps, "window_s": self._window_s, "timestamp": now})

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._zones), "events": self._count,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "zones": {s: len(z) for s, z in self._zones.items()}}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            # هيكليّة: لا مصدر بعد — تنتظر جسر بايننس حتى أوّل حدث.
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_BINANCE_BRIDGE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="LIQUIDATION_FEED_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="symbols=%d events=%d" % (len(self._zones), self._count),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "events": self._count}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._count = int(state.get("events", 0))
