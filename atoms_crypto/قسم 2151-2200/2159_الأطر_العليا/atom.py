from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.htf.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    """سياق الأطر العليا — خريطة 4H وبنية 15د (ضابط رتبة، لا بوّابة منع).

    موقع معركة الـ5د من الحرب الكبرى، من إطارين اثنين على نفس الناقل:

    • خريطة 4H (نافذة 14 يومًا): المدى (أدنى..أعلى) وموضع السعر منه ٪ وميلُ
      آخر 16 ساعة. طرفا المدى (≥85٪ أو ≤15٪) منطقتا تمدّد وانعكاس كبرى —
      شلال يوم التأسيس ضرب عند 92٪ من المدى، منطقة كانت عمياء في خريطة
      الجلسة وحدها.
    • بنية 15د (نافذتا ساعتين): مقارنة قمم/قيعان آخر 8 شموع بالثماني قبلها
      ⇒ UP (أعلى-أعلى + أعلى-أدنى) · DOWN (أدنى-أدنى + أدنى-أعلى) · MIXED.

    الرتبة: توافق بنية 15د مع جهة الصفقة وسعرٌ خارج التطرّف ⇒ A (أهداف كاملة،
    تمديد راكض)؛ تعارضٌ أو سعرٌ في التطرّف ⇒ B (أهداف محافظة، جنيٌ أسرع). تُنشَر
    grade_long وgrade_short معًا فيقرأ الحكَم رتبة صفقته. **ممنوع بنيًا** اشتراط
    إجماع الأطر للدخول — القرار للـ5د حصرًا، وهذه الحاسّة تعدِّل الرتبة فقط."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._tf_map = "4h"
        self._tf_struct = "15m"
        self._range_bars = 84
        self._slope_bars = 4
        self._struct_bars = 8
        self._extreme_high = 85.0
        self._extreme_low = 15.0
        self._max_age_s = 1200.0
        # (symbol, frame) → deque[(period_start, open, high, low, close)]
        self._win: dict[tuple[str, str], Deque[tuple[float, float | None, float, float, float]]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._counts: dict[str, int] = {}
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._tf_map = str(cfg.get("timeframe_map", "4h"))
        self._tf_struct = str(cfg.get("timeframe_struct", "15m"))
        self._range_bars = int(cfg.get("range_bars", 84))
        self._slope_bars = int(cfg.get("slope_bars", 4))
        self._struct_bars = int(cfg.get("struct_bars", 8))
        self._extreme_high = float(cfg.get("extreme_high_pct", 85.0))
        self._extreme_low = float(cfg.get("extreme_low_pct", 15.0))
        self._max_age_s = float(cfg.get("max_age_s", 1200.0))
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _push(self, key: tuple[str, str], row: tuple[float, float | None, float, float, float]) -> None:
        win = self._win.get(key)
        if win is None:
            span = self._range_bars if key[1] == self._tf_map else 2 * self._struct_bars
            win = deque(maxlen=max(2, span))
            self._win[key] = win
        start = row[0]
        if win and start == win[-1][0]:      # الشمعة نفسها أُعيد نشرها ⇒ تحديث لا تكرار
            win[-1] = row
        elif not win or start > win[-1][0]:  # شمعة أحدث ⇒ إلحاق
            win.append(row)
        # أقدم مما لدينا ⇒ تُهمَل (نافذة زمنية متحرّكة)

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        timeframe = str(payload.get("timeframe"))
        if timeframe not in (self._tf_map, self._tf_struct):    # رشّح بالإطار
            return
        symbol = str(payload.get("symbol") or "")
        high = _f(payload.get("high")); low = _f(payload.get("low"))
        close = _f(payload.get("close")); start = _f(payload.get("period_start"))
        if not symbol or None in (high, low, close, start):
            return
        self._push((symbol, timeframe), (start, _f(payload.get("open")), high, low, close))
        self._counts[timeframe] = self._counts.get(timeframe, 0) + 1
        now = time.time()
        self._last_at = now
        state = self._compute(symbol, payload.get("provider"), now)
        self._state[symbol] = state
        self._updates += 1
        await self._context.publish(EVENT_OUT, state)

    def _compute(self, symbol: str, provider: Any, now: float) -> dict[str, Any]:
        map_win = self._win.get((symbol, self._tf_map))
        struct_win = self._win.get((symbol, self._tf_struct))

        # ————— خريطة 4H —————
        range_low = range_high = position_pct = slope_pct = None
        slope_dir = "flat"
        map_bars = len(map_win) if map_win else 0
        if map_win:
            range_high = max(r[2] for r in map_win)
            range_low = min(r[3] for r in map_win)
            last_close = map_win[-1][4]
            ref_close = map_win[-self._slope_bars][4] if map_bars >= self._slope_bars else map_win[0][4]
            if ref_close:
                slope_pct = (last_close - ref_close) / ref_close * 100.0
                slope_dir = "up" if slope_pct > 0 else "down" if slope_pct < 0 else "flat"
            span = range_high - range_low
            position_pct = (last_close - range_low) / span * 100.0 if span > 0 else 50.0

        # ————— بنية 15د —————
        structure = "MIXED"
        lows_prev = lows_recent = highs_prev = highs_recent = None
        struct_seen = len(struct_win) if struct_win else 0
        need = 2 * self._struct_bars
        if struct_win and struct_seen >= need:
            highs = [r[2] for r in struct_win]
            lows = [r[3] for r in struct_win]
            n = self._struct_bars
            highs_recent = max(highs[-n:]); highs_prev = max(highs[-2 * n:-n])
            lows_recent = min(lows[-n:]); lows_prev = min(lows[-2 * n:-n])
            if highs_recent > highs_prev and lows_recent > lows_prev:
                structure = "UP"
            elif highs_recent < highs_prev and lows_recent < lows_prev:
                structure = "DOWN"
            else:
                structure = "MIXED"

        # ————— الاشتقاق: التطرّف والرتبة —————
        map_ready = map_bars >= self._slope_bars and range_high is not None
        struct_ready = struct_seen >= need
        ready = map_ready and struct_ready
        extreme = False
        extreme_side = None
        if position_pct is not None:
            if position_pct >= self._extreme_high:
                extreme, extreme_side = True, "high"
            elif position_pct <= self._extreme_low:
                extreme, extreme_side = True, "low"
        htf_bias = "up" if structure == "UP" else "down" if structure == "DOWN" else "mixed"
        grade_long = grade_short = None
        if ready:
            # A فقط عند توافق البنية مع الجهة وسعرٍ خارج التطرّف؛ غير ذلك B.
            grade_long = "B" if (extreme or structure != "UP") else "A"
            grade_short = "B" if (extreme or structure != "DOWN") else "A"

        return {
            "provider": provider, "symbol": symbol,
            "range_low": round(range_low, 8) if range_low is not None else None,
            "range_high": round(range_high, 8) if range_high is not None else None,
            "position_pct": round(position_pct, 2) if position_pct is not None else None,
            "slope_pct": round(slope_pct, 4) if slope_pct is not None else None,
            "slope_dir": slope_dir, "map_bars": map_bars,
            "structure": structure,
            "lows_prev": lows_prev, "lows_recent": lows_recent,
            "highs_prev": highs_prev, "highs_recent": highs_recent,
            "struct_bars_seen": struct_seen,
            "htf_bias": htf_bias, "extreme": extreme, "extreme_side": extreme_side,
            "grade_long": grade_long, "grade_short": grade_short,
            "ready": ready, "timestamp": now,
        }

    async def health_check(self) -> HealthStatus:
        details = {"symbols": len(self._state), "updates": self._updates,
                   "candles": dict(self._counts),
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "structure": {s: v["structure"] for s, v in self._state.items()}}
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
