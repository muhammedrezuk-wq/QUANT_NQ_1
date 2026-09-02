from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
EVENT_IN = "market.candle"
EVENT_OUT = "sense.cross_market.state"


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _r(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _coin(symbol: str) -> str:
    return symbol.split("_")[0] if symbol else symbol


class Atom(AtomBase):
    """تزامن السوق — هل حركة بيتكوين نظاميّة أم محليّة (تفرّدٌ يقود).

    يقيس تغيّر٪ آخر ~25 دقيقة (نافذة شموع 5د) لكل رمز مقابل المرجع (BTC)، من
    نفس الناقل. القياس الرجعيّ (60 يومًا) قنّن الحدس: **التزامن هو الحالة
    الافتراضية (96.5٪) فبلا قيمة فرز**؛ القيمة في الشذوذ —

    • تفرّد المرجع (يتحرّك وحده والأتباع سكون) ⇒ regime=local · btc_solo=True:
      المرجع يواصل (استمرار 58–61٪ مقاسًا) — إشارةٌ لا موافقةَ أتباع.
    • الانشقاق (تابعٌ يعاكس بنشاط) ⇒ يُدرَج في divergence: أقوى الوسطاء المقيسة
      (+21/+18 ن.أ) — «القائد الذي يدهس معارضةً فعليّة يثبت جديّته».
    • تزامنٌ تامّ (الكلّ يتبع) ⇒ regime=systemic: مؤكِّد سياق لا مرشِّح فرز.

    التقنين الرقميّ للتزامن: اتفاق الإشارة + نسبة المقدار ≥ follow_ratio. بيتا
    العملات الأصغر أعنف طبيعيًّا (مؤكِّد لا ناقض). حاسّة تشخيص وترقية، لا بوّابة."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._timeframe = "5m"
        self._reference = "BTC_USDT"
        self._symbols: list[str] = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        self._window_bars = 5
        self._min_move_pct = 0.3
        self._follow_ratio = 0.3
        self._diverge_pct = 0.2
        self._max_age_s = 600.0
        # symbol → deque[(period_start, open, close)]
        self._win: dict[str, Deque[tuple[float, float | None, float]]] = {}
        self._state: dict[str, Any] | None = None
        self._candles = 0
        self._updates = 0
        self._last_at: float | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._timeframe = str(cfg.get("timeframe", "5m"))
        self._reference = str(cfg.get("reference", "BTC_USDT"))
        syms = cfg.get("symbols") or ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        self._symbols = [str(s).strip() for s in syms if str(s).strip()]
        if self._reference not in self._symbols:
            self._symbols.insert(0, self._reference)
        self._window_bars = int(cfg.get("window_bars", 5))
        self._min_move_pct = float(cfg.get("min_move_pct", 0.3))
        self._follow_ratio = float(cfg.get("follow_ratio", 0.3))
        self._diverge_pct = float(cfg.get("diverge_pct", 0.2))
        self._max_age_s = float(cfg.get("max_age_s", 600.0))
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _push(self, symbol: str, row: tuple[float, float | None, float]) -> None:
        win = self._win.get(symbol)
        if win is None:
            win = deque(maxlen=max(2, self._window_bars))
            self._win[symbol] = win
        start = row[0]
        if win and start == win[-1][0]:
            win[-1] = row
        elif not win or start > win[-1][0]:
            win.append(row)

    def _change(self, symbol: str) -> float | None:
        """تغيّر٪ عبر النافذة = (إغلاق آخر شمعة − افتتاح أوّلها) ÷ الافتتاح."""
        win = self._win.get(symbol)
        if not win or len(win) < 2:
            return None
        open0 = win[0][1]
        if open0 is None:                 # مصدرٌ بلا افتتاح ⇒ إغلاق أوّل شمعة بديلًا
            open0 = win[0][2]
        close_last = win[-1][2]
        if open0 in (None, 0) or close_last is None:
            return None
        return (close_last - open0) / open0 * 100.0

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if str(payload.get("timeframe")) != self._timeframe:    # رشّح بالإطار
            return
        symbol = str(payload.get("symbol") or "")
        if symbol not in self._symbols:                          # سلة مقنَّنة
            return
        close = _f(payload.get("close")); start = _f(payload.get("period_start"))
        if not symbol or close is None or start is None:
            return
        self._push(symbol, (start, _f(payload.get("open")), close))
        self._candles += 1
        now = time.time()
        self._last_at = now
        self._state = self._compute(payload.get("provider"), now)
        self._updates += 1
        await self._context.publish(EVENT_OUT, self._state)

    def _compute(self, provider: Any, now: float) -> dict[str, Any]:
        changes = {_coin(s): self._change(s) for s in self._symbols}
        ref_change = changes.get(_coin(self._reference))
        peers = [s for s in self._symbols if s != self._reference]
        all_ready = ref_change is not None and all(changes.get(_coin(s)) is not None for s in peers)

        peer_info: dict[str, Any] = {}
        regime = "warming"
        synced = 0
        btc_solo = False
        divergence: list[str] = []

        if ref_change is None or not all_ready:
            regime = "warming"
            for s in peers:
                change = changes.get(_coin(s))
                peer_info[_coin(s)] = {"change_pct": _r(change), "ratio": None,
                                       "status": "no_data" if change is None else "lag"}
        elif abs(ref_change) < self._min_move_pct:
            regime = "quiet"                              # المرجع ساكن ⇒ لا حدث يُشخَّص
            for s in peers:
                peer_info[_coin(s)] = {"change_pct": _r(changes[_coin(s)]),
                                       "ratio": None, "status": "flat_ref"}
        else:
            for s in peers:
                change = changes[_coin(s)]
                ratio = abs(change) / abs(ref_change) if ref_change else 0.0
                same_sign = change != 0 and (change > 0) == (ref_change > 0)
                if same_sign and ratio >= self._follow_ratio:
                    status = "follow"; synced += 1
                elif change != 0 and not same_sign and abs(change) >= self._diverge_pct:
                    status = "diverge"; divergence.append(_coin(s))
                else:
                    status = "lag"                        # نفس الإشارة لكن ضعيف، أو شبه ساكن
                peer_info[_coin(s)] = {"change_pct": _r(change), "ratio": round(ratio, 2),
                                       "status": status}
            peer_count = len(peers)
            if synced == peer_count:
                regime = "systemic"                       # الكلّ يتبع ⇒ نظاميّ
            elif synced == 0:
                regime = "local"; btc_solo = True         # وحده ⇒ محليّ (تفرّد يقود)
            else:
                regime = "mixed"

        return {
            "provider": provider, "symbol": self._reference, "reference": self._reference,
            "timeframe": self._timeframe, "window_bars": self._window_bars,
            "ref_change_pct": _r(ref_change),
            "changes": {k: _r(v) for k, v in changes.items()},
            "peers": peer_info, "peer_count": len(peers), "synced_count": synced,
            "regime": regime, "btc_solo": btc_solo, "divergence": divergence,
            "timestamp": now,
        }

    async def health_check(self) -> HealthStatus:
        details = {"tracked": len(self._symbols), "seen": len(self._win),
                   "candles": self._candles, "updates": self._updates,
                   "age_s": (time.time() - self._last_at) if self._last_at else None,
                   "regime": self._state.get("regime") if self._state else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._last_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message="AWAITING_FIRST_CANDLE", details=details)
        if details["age_s"] is not None and details["age_s"] > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CANDLE_STALE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="seen=%d updates=%d" % (len(self._win), self._updates),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "updates": self._updates}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._updates = int(state.get("updates", 0))
