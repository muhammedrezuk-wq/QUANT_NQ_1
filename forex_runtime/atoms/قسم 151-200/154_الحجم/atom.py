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
EVENT_OUT = "analysis.volume.state"

METHOD = "relative_volume"
SOURCE_TICK = "tick"

SIGNAL_ACCUM = "accumulation"
SIGNAL_DISTRIB = "distribution"
SIGNAL_NORMAL = "normal"

TREND_RISING = "rising"
TREND_FALLING = "falling"
TREND_FLAT = "flat"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"
WARN_NO_VOLUME = "no_volume_in_candle"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"
REASON_NO_VOLUME = "NO_VOLUME_IN_CANDLES"

_SCORE_MAX = 100.0
_RATIO_SCALE = 33.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@section_atom("150", "154")
@live_analyzer("volume", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._baseline_window = 50
        self._trend_short = 5
        self._trend_long = 20
        self._high_mult = 1.5
        self._spike_mult = 2.5
        self._min_candles = 20
        self._state: dict[tuple, dict[str, Any]] = {}
        self._candles_seen = 0
        self._emitted = 0
        self._no_volume = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._baseline_window = int(cfg["baseline_window"])
        self._trend_short = int(cfg["trend_short"])
        self._trend_long = int(cfg["trend_long"])
        self._high_mult = float(cfg["high_mult"])
        self._spike_mult = float(cfg["spike_mult"])
        self._min_candles = int(cfg["min_candles"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping this atom's own volume history on every
    # restart. Measured effect: `min_candles` (20 by default) closed candles
    # are required before it leaves insufficient_data, so a reboot silenced
    # volume for 20 candles every time. The wrapper now chains both states --
    # "live_analysis" for its own tick memory, "atom" for this history -- so
    # this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "no_volume": self._no_volume,
            "scopes": [{"symbol": symbol, "timeframe": timeframe,
                        "vol": list(st["vol"]), "prev_close": st["prev_close"]}
                       for (symbol, timeframe), st in self._state.items()],
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._candles_seen = int(state.get("candles_seen") or 0)
        self._emitted = int(state.get("emitted") or 0)
        self._no_volume = int(state.get("no_volume") or 0)
        self._state = {}
        for row in state.get("scopes") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            vol: deque = deque(maxlen=self._baseline_window * 5)
            for value in row.get("vol") or []:
                number = _to_float(value)
                if number is not None:
                    vol.append(number)
            self._state[(symbol, str(row.get("timeframe") or ""))] = {
                "vol": vol, "prev_close": _to_float(row.get("prev_close")),
            }

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        volume = _to_float(payload.get("volume"))
        close = _to_float(payload.get("close"))
        if not symbol or volume is None or close is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe,
                                period_start=period_start)
        # Owner stamp 2026-08-25: the live tick carries no volume field at all
        # (measured on 2,142,509 stored ticks), so 103 builds every candle with
        # volume = 0.0 -- a number, not an absence. That zero passed the
        # "is None" guard above, so this atom used to accumulate a baseline of
        # zeros and publish a permanent "flat" volume trend while carrying real
        # weight in the fast blend. A fabricated reading is worse than a
        # missing one: it cannot be told apart from a measured flat market.
        # Non-positive volume is now refused AND counted -- never dropped in
        # silence -- so the section sees an absent contributor, not a lie.
        if volume <= 0.0:
            self._no_volume += 1
            # Owner stamp 2026-08-25 (second pass): returning in silence here
            # was measured to KILL the whole slow path. The section merger
            # completes a candle cycle only when ALL FIFTEEN analysts deliver,
            # so one silent abstention froze it -- zero candle cycles, zero
            # fusions, and the decision aggregator moved its missing section to
            # 150. Measured: 7 candles closed, 14 of 15 analysts delivered.
            # A declared absence still counts as a delivery; only silence does
            # not. Same shape 164 already uses for the identical missing input.
            await self._emit_no_volume(symbol, timeframe, cycle_id)
            return
        key = (symbol, timeframe)
        st = self._state.get(key)
        if st is None:
            st = {"vol": deque(maxlen=self._baseline_window * 5), "prev_close": None}
            self._state[key] = st
        prev_close = st["prev_close"]
        st["vol"].append(volume)
        st["prev_close"] = close
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, volume, close, prev_close, st,
                         str(payload.get("account_id") or ""))

    def _trend(self, vols: list[float], w_short: int, w_long: int) -> str:
        if len(vols) < w_long:
            return TREND_FLAT
        short = _mean(vols[-w_short:])
        long = _mean(vols[-w_long:])
        if long <= 0:
            return TREND_FLAT
        if short > long * self._high_mult:
            return TREND_RISING
        if short < long / self._high_mult:
            return TREND_FALLING
        return TREND_FLAT

    async def _emit_no_volume(self, symbol: str, timeframe: str,
                              cycle_id: str) -> None:
        """A declared absence -- a delivery that says 'this candle carried no
        volume', never a fabricated reading and never silence."""
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "volume", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_INSUFFICIENT, "signal": SIGNAL_NORMAL,
            "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
            "warnings": [WARN_NO_VOLUME],
            "metadata": {"method": METHOD, "timeframe": timeframe,
                         "source": SOURCE_TICK, "volume_present": False}})
        self._emitted += 1

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, volume: float,
                    close: float, prev_close: float | None, st: dict[str, Any],
                    account: str = "") -> None:
        if self._context is None:
            return
        base = {"symbol": symbol, "id": "volume", "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe, "source": SOURCE_TICK}
        w_base = _speed_window(self._baseline_window, symbol, account, 5)
        vols = list(st["vol"])[-w_base:]
        w_min = _speed_window(self._min_candles, symbol, account, 6)
        if len(vols) < w_min:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_NORMAL,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        avg_volume = _mean(vols)
        ratio = volume / avg_volume if avg_volume > 0 else 1.0
        spike = ratio >= self._spike_mult
        rising_price = prev_close is not None and close > prev_close
        falling_price = prev_close is not None and close < prev_close
        if ratio >= self._high_mult and rising_price:
            signal = SIGNAL_ACCUM
        elif ratio >= self._high_mult and falling_price:
            signal = SIGNAL_DISTRIB
        else:
            signal = SIGNAL_NORMAL
        score = int(round(min(_SCORE_MAX, ratio * _RATIO_SCALE)))
        confidence = round(min(1.0, len(vols) / w_base), 2)
        meta.update({"volume": round(volume, 2), "avg_volume": round(avg_volume, 2),
                     "ratio": round(ratio, 2), "spike": spike,
                     "volume_trend": self._trend(
                         vols, _speed_window(self._trend_short, symbol, account, 3),
                         _speed_window(self._trend_long, symbol, account, 6))})
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            if self._no_volume:
                return HealthStatus(
                    state=HealthState.DEGRADED,
                    message="%s: refused=%d" % (REASON_NO_VOLUME, self._no_volume),
                    details={"tracked": len(self._state),
                             "no_volume": self._no_volume})
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES,
                                details={"tracked": len(self._state)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d tracked=%d no_volume=%d" % (
                self._candles_seen, self._emitted, len(self._state),
                self._no_volume),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "tracked": len(self._state), "no_volume": self._no_volume})
