from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.min1_lens.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """عدسة الدقيقة الواحدة — نفس السوق بدقّة ×5 (توقيتٌ لا اتجاه).

    تنشر آخر display شمعة 1د: اتجاه (+/−) وOHLC والحجم النسبيّ (حجم الشمعة ÷
    MA20 الحجم). ترى تبدّل الأيدي داخل شمعة الـ5د قبل اكتمالها بدقائق: أين توقّف
    الاندفاع بالضبط، أيُدافَع عن القاع الجاري أم يتفتّت، وأين وقفت آخر محاولة.

    **أداة عيون لا أداة قرار — لا عتبات حكم فيها بالتصميم.** القاعدة الحاكمة:
    العدسة لا تنقض إغلاق الـ5د أبدًا؛ أزندة النظام كلّها بإغلاقات 5د وهذه توقّت
    داخلها فقط. ضجيج الدقيقة كثيف: قراءة اتجاهٍ منها مباشرةً خطأ منهجيّ. (كشفت
    مقاسًا انهيار ما بعد خروج الراكض بشمعتَي ×1.7/×1.9، ونابض 79,873 تحت 80,000.)"""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "1m"
        self._display = 15
        self._vol_ma = 20
        self._max_age_s = 180.0
        # symbol → deque[(period_start, open, high, low, close, volume)]
        self._win: dict[str, Deque[tuple[float, float | None, float | None, float | None, float | None, float | None]]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._timeframe = str(cfg.get("timeframe", "1m"))
        self._display = int(cfg.get("display", 15))
        self._vol_ma = int(cfg.get("vol_ma", 20))
        self._max_age_s = float(cfg.get("max_age_s", 180.0))
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _push(self, symbol: str, row: tuple) -> Deque:
        win = self._win.get(symbol)
        if win is None:
            win = deque(maxlen=max(2, self._display + self._vol_ma + 1))
            self._win[symbol] = win
        start = row[0]
        if win and start == win[-1][0]:
            win[-1] = row
        elif not win or start > win[-1][0]:
            win.append(row)
        return win

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if str(payload.get("timeframe")) != self._timeframe:    # رشّح بالإطار
            return
        symbol = str(payload.get("symbol") or "")
        close = _f(payload.get("close")); start = _f(payload.get("period_start"))
        if not symbol or close is None or start is None:
            return
        win = self._push(symbol, (start, _f(payload.get("open")), _f(payload.get("high")),
                                  _f(payload.get("low")), close, _f(payload.get("volume"))))
        now = time.time()
        self._last_at = now
        state = self._compute(symbol, win, payload.get("provider"), now)
        self._state[symbol] = state
        self._updates += 1
        await self._context.publish(EVENT_OUT, state)

    def _compute(self, symbol: str, win: Deque, provider: Any, now: float) -> dict[str, Any]:
        vols = [r[5] for r in win if r[5] is not None]
        # MA20 الحجم — العشرون السابقة لآخر شمعة (توافقًا مع أداة الدقيقة الأصل).
        if len(vols) > self._vol_ma + 1:
            window = vols[-(self._vol_ma + 1):-1]
            vma = sum(window) / len(window)
        elif vols:
            vma = sum(vols) / len(vols)
        else:
            vma = 0.0
        bars: list[dict[str, Any]] = []
        for start, o, h, l, c, v in list(win)[-self._display:]:
            direction = None if (o is None or c is None) else ("+" if c >= o else "-")
            vol_x = (v / vma) if (vma and v is not None) else 0.0
            bars.append({"period_start": start, "dir": direction,
                         "open": o, "high": h, "low": l, "close": c,
                         "volume": v, "vol_x": round(vol_x, 2)})
        return {
            "provider": provider, "symbol": symbol,
            "last_price": win[-1][4], "vol_ma": self._vol_ma, "vma": round(vma, 4),
            "bars": bars, "count": len(bars), "timestamp": now,
        }

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "last": {s: v["last_price"] for s, v in self._state.items()}}
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
