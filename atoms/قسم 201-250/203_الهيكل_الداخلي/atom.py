from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"

EVENT_IN = "structure.swing.state"
EVENT_OUT = "structure.internal.state"

METHOD = "swing_pullback"
ID_INTERNAL = "internal"

SWING_HIGH = "swing_high"
SWING_LOW = "swing_low"

SIGNAL_HL = "HL"
SIGNAL_LH = "LH"
SIGNAL_NONE = "none"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_NO_STRUCTURE = "no_structure_yet"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_SWING_INPUT_YET"

_PRICE_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _round(value: Any) -> Any:
    return round(value, _PRICE_DP) if value is not None else None


@section_atom("200", "203")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._state: dict[tuple, dict[str, Any]] = {}
        self._inputs_seen = 0
        self._swings_seen = 0
        self._events = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_swing)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_swing(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        self._inputs_seen += 1
        cycle_id = str(payload.get("cycle_id", ""))
        meta_in = payload.get("metadata") or {}
        timeframe = str(payload.get("timeframe", "") or meta_in.get("timeframe", ""))
        close = _to_float(meta_in.get("close"))
        swing_signal = payload.get("signal")
        swing_price = _to_float(meta_in.get("price"))
        swing_time = meta_in.get("swing_time")
        swing_score = payload.get("score", 0)
        key = (symbol, timeframe)
        st = self._state.get(key)
        if st is None:
            st = {"sh": None, "sh_t": None, "sl": None, "sl_t": None}
            self._state[key] = st
        event = SIGNAL_NONE
        score = 0
        if swing_signal == SWING_HIGH and swing_price is not None:
            prev = st["sh"]
            if prev is not None and swing_price < prev:
                event = SIGNAL_LH
                score = swing_score
            st["sh"] = swing_price
            st["sh_t"] = swing_time
            self._swings_seen += 1
        elif swing_signal == SWING_LOW and swing_price is not None:
            prev = st["sl"]
            if prev is not None and swing_price > prev:
                event = SIGNAL_HL
                score = swing_score
            st["sl"] = swing_price
            st["sl_t"] = swing_time
            self._swings_seen += 1
        if event != SIGNAL_NONE:
            self._events += 1
        await self._emit(symbol, timeframe, cycle_id, close, st, event, score)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, close: Any,
                    st: dict[str, Any], event: str, score: Any) -> None:
        if self._context is None:
            return
        base = {"symbol": symbol, "id": ID_INTERNAL, "cycle_id": cycle_id,
                "timeframe": timeframe}
        meta = {"method": METHOD, "timeframe": timeframe,
                "swing_high": _round(st["sh"]), "swing_high_time": st["sh_t"],
                "swing_low": _round(st["sl"]), "swing_low_time": st["sl_t"],
                "close": close}
        if st["sh"] is None and st["sl"] is None:
            await self._context.publish(EVENT_OUT, {
                **base, "status": STATUS_INSUFFICIENT, "signal": SIGNAL_NONE,
                "score": 0, "confidence": 0.0, "quality": QUALITY_LOW,
                "warnings": [WARN_NO_STRUCTURE], "metadata": meta})
            self._emitted += 1
            return
        confidence = 1.0 if event != SIGNAL_NONE else 0.0
        await self._context.publish(EVENT_OUT, {
            **base, "status": STATUS_OK, "signal": event, "score": int(score),
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._inputs_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details={"tracked": len(self._state)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="inputs=%d swings=%d events=%d tracked=%d" % (
                self._inputs_seen, self._swings_seen, self._events, len(self._state)),
            details={"inputs": self._inputs_seen, "swings": self._swings_seen,
                     "events": self._events, "emitted": self._emitted,
                     "tracked": len(self._state)})
