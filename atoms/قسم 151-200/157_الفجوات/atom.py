from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.gap.state"

METHOD = "gap"

SIGNAL_GAP_UP = "gap_up"
SIGNAL_GAP_DOWN = "gap_down"
SIGNAL_FILLED = "filled"
SIGNAL_NONE = "none"

TYPE_UP = "up"
TYPE_DOWN = "down"
TYPE_NONE = "none"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_PERCENT = 100.0
_SCORE_MAX = 100.0
_GAP_SCALE = 40.0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("150", "157")
@live_analyzer("gap", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._gap_threshold_pct = 0.1
        self._state: dict[tuple, dict[str, Any]] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._gap_threshold_pct = float(context.config["gap_threshold_pct"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping the previous close and any still-open gap on
    # every restart. Measured effect: an unfilled gap tracked across many
    # candles (its "age") reset to none on every reboot, so a gap that was
    # about to be reported as filled quietly vanished instead. The wrapper
    # now chains both states -- "live_analysis" for its own tick memory,
    # "atom" for this gap memory -- so this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "scopes": [{"symbol": symbol, "timeframe": timeframe,
                        "prev_close": st["prev_close"], "open_gap": st["open_gap"]}
                       for (symbol, timeframe), st in self._state.items()],
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._candles_seen = int(state.get("candles_seen") or 0)
        self._emitted = int(state.get("emitted") or 0)
        self._state = {}
        for row in state.get("scopes") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            open_gap = None
            raw_gap = row.get("open_gap")
            if isinstance(raw_gap, dict):
                level = _to_float(raw_gap.get("level"))
                gap_type = str(raw_gap.get("type") or "")
                age = raw_gap.get("age")
                if (level is not None and gap_type in (TYPE_UP, TYPE_DOWN)
                        and isinstance(age, (int, float))):
                    open_gap = {"level": level, "type": gap_type, "age": int(age)}
            self._state[(symbol, str(row.get("timeframe") or ""))] = {
                "prev_close": _to_float(row.get("prev_close")), "open_gap": open_gap,
            }

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        o = _to_float(payload.get("open"))
        high = _to_float(payload.get("high"))
        low = _to_float(payload.get("low"))
        close = _to_float(payload.get("close"))
        if not symbol or o is None or high is None or low is None or close is None:
            return
        symbol = str(symbol)
        timeframe = str(payload.get("timeframe", ""))
        period_start = payload.get("period_start", payload.get("timestamp", ""))
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        st = self._state.get(key)
        if st is None:
            st = {"prev_close": None, "open_gap": None}
            self._state[key] = st
        self._candles_seen += 1
        await self._emit(symbol, timeframe, cycle_id, o, high, low, close, st)
        st["prev_close"] = close

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, o: float,
                    high: float, low: float, close: float, st: dict[str, Any]) -> None:
        if self._context is None:
            return
        base = {"symbol": symbol, "id": "gap", "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe}
        prev_close = st["prev_close"]
        if prev_close is None or prev_close <= 0:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_NONE,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_INSUFFICIENT], "metadata": meta})
            self._emitted += 1
            return
        gap_pct = (o - prev_close) / prev_close * _PERCENT
        filled_now = False
        open_gap = st["open_gap"]
        if open_gap is not None:
            open_gap["age"] += 1
            if low <= open_gap["level"] <= high:
                filled_now = True
                st["open_gap"] = None
        if gap_pct >= self._gap_threshold_pct:
            gap_type, signal = TYPE_UP, SIGNAL_GAP_UP
            st["open_gap"] = {"level": prev_close, "type": gap_type, "age": 0}
        elif gap_pct <= -self._gap_threshold_pct:
            gap_type, signal = TYPE_DOWN, SIGNAL_GAP_DOWN
            st["open_gap"] = {"level": prev_close, "type": gap_type, "age": 0}
        elif filled_now:
            gap_type, signal = TYPE_NONE, SIGNAL_FILLED
        else:
            gap_type, signal = TYPE_NONE, SIGNAL_NONE
        is_gap = signal in (SIGNAL_GAP_UP, SIGNAL_GAP_DOWN)
        score = int(round(min(_SCORE_MAX, abs(gap_pct) * _GAP_SCALE))) if is_gap else 0
        confidence = 1.0 if signal != SIGNAL_NONE else 0.0
        tracked = st["open_gap"]
        meta.update({"gap_pct": round(gap_pct, 4), "gap_size": round(abs(o - prev_close), 5),
                     "gap_type": gap_type, "filled": filled_now,
                     "open_gap_age": tracked["age"] if tracked else 0})
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES,
                                details={"tracked": len(self._state)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d tracked=%d" % (
                self._candles_seen, self._emitted, len(self._state)),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "tracked": len(self._state)})
