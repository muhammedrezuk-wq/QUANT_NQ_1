from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.relative_strength.state"

METHOD = "basket_rank"

SIG_STRONG = "strong"
SIG_WEAK = "weak"
SIG_NEUTRAL = "neutral"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_peers"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_PERCENT = 100.0
_MIN_PEERS = 3
_CONF_SIGNAL = 0.7
_CONF_NEUTRAL = 0.4
_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("150", "161")
@live_analyzer("relative_strength", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window = 20
        self._strong_pct = 0.66
        self._weak_pct = 0.33
        self._state: dict[tuple, dict[str, Any]] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._window = int(cfg["window"])
        self._strong_pct = float(cfg["strong_pct"])
        self._weak_pct = float(cfg["weak_pct"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping this atom's per-symbol close history on every
    # restart. Measured effect: `window`+1 (21 with the default config)
    # closed candles are required before a symbol leaves insufficient_data,
    # so a reboot silenced relative-strength ranking for 21 candles every
    # time. The wrapper now chains both states -- "live_analysis" for its
    # own tick memory, "atom" for this close history -- so this one
    # survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "scopes": [{"symbol": symbol, "timeframe": timeframe,
                        "closes": list(st["closes"]), "ret": st["ret"]}
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
            closes: deque = deque(maxlen=self._window + 1)
            for value in row.get("closes") or []:
                number = _to_float(value)
                if number is not None:
                    closes.append(number)
            self._state[(symbol, str(row.get("timeframe") or ""))] = {
                "closes": closes, "ret": _to_float(row.get("ret")),
            }

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
        self._candles_seen += 1
        st = self._state.get(key)
        if st is None:
            st = {"closes": deque(maxlen=self._window + 1), "ret": None}
            self._state[key] = st
        st["closes"].append(close)
        if len(st["closes"]) <= self._window:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        first = st["closes"][0]
        if not first:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        ret = (close - first) / first
        st["ret"] = ret
        peers = [s["ret"] for (sym2, tf2), s in self._state.items()
                 if tf2 == timeframe and s["ret"] is not None]
        total = len(peers)
        if total < _MIN_PEERS:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        rank = sum(1 for r in peers if r < ret)
        percentile = rank / (total - 1)
        if percentile >= self._strong_pct:
            signal = SIG_STRONG
        elif percentile <= self._weak_pct:
            signal = SIG_WEAK
        else:
            signal = SIG_NEUTRAL
        score = int(round(percentile * _PERCENT))
        confidence = _CONF_NEUTRAL if signal == SIG_NEUTRAL else _CONF_SIGNAL
        await self._emit(symbol, timeframe, cycle_id, signal, score, confidence,
                         ret, rank, total, percentile)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, signal: str,
                    score: int, confidence: float, ret: float, rank: int,
                    total: int, percentile: float) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "relative_strength", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe,
                         "window_return": round(ret, _DP), "rank": rank,
                         "peers": total, "percentile": round(percentile, _DP)}})
        self._emitted += 1

    async def _emit_insufficient(self, symbol: str, timeframe: str,
                                 cycle_id: str) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "relative_strength", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_INSUFFICIENT, "signal": SIG_NEUTRAL, "score": 0,
            "confidence": 0.0, "quality": QUALITY_LOW, "warnings": [WARN_INSUFFICIENT],
            "metadata": {"method": METHOD, "timeframe": timeframe}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._candles_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_CANDLES)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="candles=%d emitted=%d tracked=%d" % (
                self._candles_seen, self._emitted, len(self._state)),
            details={"candles": self._candles_seen, "emitted": self._emitted,
                     "tracked": len(self._state)})
