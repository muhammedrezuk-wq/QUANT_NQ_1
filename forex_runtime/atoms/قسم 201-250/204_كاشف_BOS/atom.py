from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"

EVENT_IN = "structure.external.state"
EVENT_OUT = "structure.bos.state"

METHOD = "close_break_continuation"
ID_BOS = "bos"

DIR_UP = "up"
DIR_DOWN = "down"

SIGNAL_BOS = "bos"
SIGNAL_NONE = "none"

STATUS_OK = "ok"

QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_STRUCTURE_INPUT_YET"

_SCORE_MAX = 100.0
_LEVEL_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _round(value: Any) -> Any:
    return round(value, _LEVEL_DP) if value is not None else None


@section_atom("200", "204")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._state: dict[tuple, dict[str, Any]] = {}
        self._inputs_seen = 0
        self._breaks = 0
        self._bos = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_external)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_external(self, payload: dict[str, Any]) -> None:
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
        swing_high = _to_float(meta_in.get("swing_high"))
        swing_low = _to_float(meta_in.get("swing_low"))
        sh_t = meta_in.get("swing_high_time")
        sl_t = meta_in.get("swing_low_time")
        key = (symbol, timeframe)
        st = self._state.get(key)
        if st is None:
            st = {"bh_t": None, "bl_t": None, "dir": None}
            self._state[key] = st
        break_dir = None
        level = None
        if close is not None and swing_high is not None and close > swing_high and sh_t != st["bh_t"]:
            break_dir = DIR_UP
            level = swing_high
            st["bh_t"] = sh_t
        elif close is not None and swing_low is not None and close < swing_low and sl_t != st["bl_t"]:
            break_dir = DIR_DOWN
            level = swing_low
            st["bl_t"] = sl_t
        signal = SIGNAL_NONE
        direction = None
        out_level = None
        score = 0
        if break_dir is not None:
            self._breaks += 1
            is_bos = st["dir"] is not None and break_dir == st["dir"]
            st["dir"] = break_dir
            if is_bos:
                signal = SIGNAL_BOS
                direction = break_dir
                out_level = level
                score = self._score(close, level, swing_high, swing_low)
                self._bos += 1
        await self._emit(symbol, timeframe, cycle_id, close, signal, direction, out_level, score)

    def _score(self, close: float, level: float, swing_high: Any, swing_low: Any) -> int:
        if swing_high is None or swing_low is None:
            return 0
        span = swing_high - swing_low
        if span <= 0:
            return 0
        frac = abs(close - level) / span
        return int(round(min(1.0, frac) * _SCORE_MAX))

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, close: Any,
                    signal: str, direction: Any, level: Any, score: int) -> None:
        if self._context is None:
            return
        confidence = 1.0 if signal == SIGNAL_BOS else 0.0
        meta = {"method": METHOD, "timeframe": timeframe, "direction": direction,
                "level": _round(level), "close": close}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_BOS, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": score,
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
            message="inputs=%d breaks=%d bos=%d tracked=%d" % (
                self._inputs_seen, self._breaks, self._bos, len(self._state)),
            details={"inputs": self._inputs_seen, "breaks": self._breaks,
                     "bos": self._bos, "emitted": self._emitted,
                     "tracked": len(self._state)})
