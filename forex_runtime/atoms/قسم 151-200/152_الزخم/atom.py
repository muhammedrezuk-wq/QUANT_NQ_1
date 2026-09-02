from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.analysis_speed import speed_factor, speed_value
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"


def _speed_window(base: int, symbol: str, account: str, floor: int) -> int:
    """نافذة مشتقة من مفتاح سرعة التحليل — «جماعة الشموع» بنفس المفتاح (٢٦-٠٨).

    الأساس = قيمة المانيفست (نقطة التطابق 50 = اليوم حرفيًّا) · السقف الأساس×5."""
    factor = speed_factor(speed_value(account, symbol))
    return max(floor, min(base * 5, int(round(base * factor))))

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.momentum.state"

METHOD = "roc"

SIGNAL_UP = "up"
SIGNAL_DOWN = "down"
SIGNAL_SIDEWAYS = "sideways"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

LEVEL_WEAK = "weak"
LEVEL_MEDIUM = "medium"
LEVEL_STRONG = "strong"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_PERCENT = 100.0
_SCORE_MAX = 100.0
_ROC_SCALE = 8.0
_RANDOM_PERSISTENCE = 0.5
_PERSISTENCE_SPAN = 2.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("150", "152")
@live_analyzer("momentum", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._roc_period = 10
        self._impulse_window = 14
        self._persistence_window = 10
        self._roc_flat_pct = 0.05
        self._persistence_min = 0.6
        self._strong_score = 70.0
        self._medium_score = 40.0
        self._min_candles = 20
        self._need = 21
        self._closes: dict[tuple, deque] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._roc_period = int(cfg["roc_period"])
        self._impulse_window = int(cfg["impulse_window"])
        self._persistence_window = int(cfg["persistence_window"])
        self._roc_flat_pct = float(cfg["roc_flat_pct"])
        self._persistence_min = float(cfg["persistence_min"])
        self._strong_score = float(cfg["strong_score"])
        self._medium_score = float(cfg["medium_score"])
        self._min_candles = int(cfg["min_candles"])
        self._need = max(self._roc_period, self._impulse_window,
                         self._persistence_window, self._min_candles) + 1
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, so this atom's own close history was wiped on every
    # restart. Measured effect: the atom needs `_need` (21 with the default
    # config) closed candles before it leaves insufficient_data, so a reboot
    # silenced momentum for 21 candles every time. The wrapper now chains
    # both states -- "live_analysis" for its own tick memory, "atom" for
    # this history -- so this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            # A tuple key cannot survive JSON: split into explicit fields.
            "scopes": [{"symbol": symbol, "timeframe": timeframe,
                        "closes": list(closes)}
                       for (symbol, timeframe), closes in self._closes.items()],
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._candles_seen = int(state.get("candles_seen") or 0)
        self._emitted = int(state.get("emitted") or 0)
        self._closes = {}
        for row in state.get("scopes") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            closes: deque = deque(maxlen=self._need * 5)
            for value in row.get("closes") or []:
                number = _to_float(value)
                if number is not None:
                    closes.append(number)
            if closes:
                self._closes[(symbol, str(row.get("timeframe") or ""))] = closes

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        close = _to_float(payload.get("close"))
        if not symbol or close is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        closes = self._closes.get(key)
        if closes is None:
            closes = deque(maxlen=self._need * 5)
            self._closes[key] = closes
        closes.append(close)
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, closes,
                         str(payload.get("account_id") or ""))

    def _roc(self, closes: deque, w: int) -> float:
        past = closes[-(w + 1)]
        if past == 0:
            return 0.0
        return (closes[-1] - past) / past * _PERCENT

    def _impulse(self, closes: deque, w: int) -> float:
        moves = [abs(closes[i] - closes[i - 1]) for i in range(-w, 0)]
        avg = sum(moves) / len(moves) if moves else 0.0
        last = abs(closes[-1] - closes[-2])
        return last / avg if avg > 0 else 0.0

    def _persistence(self, closes: deque, w: int) -> float:
        ups = downs = 0
        for i in range(-w, 0):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                ups += 1
            elif diff < 0:
                downs += 1
        total = ups + downs
        if total == 0:
            return _RANDOM_PERSISTENCE
        return max(ups, downs) / total

    def _level(self, score: int, signal: str) -> str:
        if signal == SIGNAL_SIDEWAYS or score < self._medium_score:
            return LEVEL_WEAK
        if score >= self._strong_score:
            return LEVEL_STRONG
        return LEVEL_MEDIUM

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    closes: deque, account: str = "") -> None:
        if self._context is None:
            return
        base = {"symbol": symbol, "id": "momentum", "cycle_id": cycle_id,
                "timeframe": timeframe}
        w_roc = _speed_window(self._roc_period, symbol, account, 2)
        w_impulse = _speed_window(self._impulse_window, symbol, account, 3)
        w_persistence = _speed_window(self._persistence_window, symbol, account, 3)
        w_min = _speed_window(self._min_candles, symbol, account, 6)
        need = max(w_roc, w_impulse, w_persistence, w_min) + 1
        meta = {"method": METHOD, "timeframe": timeframe, "roc_period": w_roc}
        if len(closes) < need:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_SIDEWAYS,
                "score": 0, "confidence": 0.0, "level": LEVEL_WEAK,
                "quality": QUALITY_LOW, "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        roc = self._roc(closes, w_roc)
        impulse = self._impulse(closes, w_impulse)
        persistence = self._persistence(closes, w_persistence)
        if abs(roc) <= self._roc_flat_pct or persistence < self._persistence_min:
            signal = SIGNAL_SIDEWAYS
        elif roc > 0:
            signal = SIGNAL_UP
        else:
            signal = SIGNAL_DOWN
        score = 0 if signal == SIGNAL_SIDEWAYS else int(round(
            min(_SCORE_MAX, abs(roc) * _ROC_SCALE)))
        level = self._level(score, signal)
        confidence = round(max(0.0, (persistence - _RANDOM_PERSISTENCE) * _PERSISTENCE_SPAN), 2)
        meta.update({"roc": round(roc, 4), "impulse": round(impulse, 2),
                     "persistence": round(persistence, 2)})
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "level": level, "quality": QUALITY_GOOD,
            "warnings": [], "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES,
                                details={"tracked": len(self._closes)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d tracked=%d" % (
                self._candles_seen, self._emitted, len(self._closes)),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "tracked": len(self._closes)})
