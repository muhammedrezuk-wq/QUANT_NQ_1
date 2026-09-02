from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.live_analysis import live_analyzer
from shared.section_contract import section_atom
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "2.5.0"

EVENT_IN = "market_data.candle_closed"
EVENT_OUT = "analysis.noise.state"

METHOD = "efficiency_ratio"

LEVEL_NOISY = "noisy"
LEVEL_NORMAL = "normal"
LEVEL_EFFICIENT = "efficient"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_INSUFFICIENT = "insufficient_candles"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"

_PERCENT = 100.0
_CONF_SIGNAL = 0.7
_CONF_NORMAL = 0.4
_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("150", "165")
@live_analyzer("noise", EVENT_OUT)
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window = 10
        self._noisy_max = 0.3
        self._efficient_min = 0.6
        self._state: dict[tuple, deque] = {}
        self._candles_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._window = int(cfg["window"])
        self._noisy_max = float(cfg["noisy_max"])
        self._efficient_min = float(cfg["efficient_min"])
        context.subscribe(EVENT_IN, self._on_candle)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    # Owner stamp 2026-08-21: the live wrapper used to REPLACE snapshot/
    # restore outright, wiping this atom's own close history on every
    # restart. Measured effect: `window`+1 (11 with the default config)
    # closed candles are required before it leaves insufficient_data, so a
    # reboot silenced noise for 11 candles every time. The wrapper now
    # chains both states -- "live_analysis" for its own tick memory, "atom"
    # for this close history -- so this one survives a restart.
    async def snapshot(self) -> dict[str, Any]:
        return {
            "candles_seen": self._candles_seen,
            "emitted": self._emitted,
            "scopes": [{"symbol": symbol, "timeframe": timeframe, "closes": list(closes)}
                       for (symbol, timeframe), closes in self._state.items()],
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
            if closes:
                self._state[(symbol, str(row.get("timeframe") or ""))] = closes

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
        closes = self._state.get(key)
        if closes is None:
            closes = deque(maxlen=self._window + 1)
            self._state[key] = closes
        closes.append(close)
        if len(closes) <= self._window:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        net = abs(closes[-1] - closes[0])
        path = 0.0
        for i in range(1, len(closes)):
            path += abs(closes[i] - closes[i - 1])
        if path <= 0.0:
            await self._emit_insufficient(symbol, timeframe, cycle_id)
            return
        efficiency = net / path
        if efficiency < self._noisy_max:
            signal = LEVEL_NOISY
        elif efficiency > self._efficient_min:
            signal = LEVEL_EFFICIENT
        else:
            signal = LEVEL_NORMAL
        score = int(round(efficiency * _PERCENT))
        confidence = _CONF_NORMAL if signal == LEVEL_NORMAL else _CONF_SIGNAL
        await self._emit(symbol, timeframe, cycle_id, signal, score, confidence,
                         efficiency, net, path)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str, signal: str,
                    score: int, confidence: float, efficiency: float, net: float,
                    path: float) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "noise", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": signal, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe,
                         "efficiency_ratio": round(efficiency, _DP),
                         "net_move": round(net, _DP), "total_path": round(path, _DP)}})
        self._emitted += 1

    async def _emit_insufficient(self, symbol: str, timeframe: str,
                                 cycle_id: str) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": "noise", "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_INSUFFICIENT, "signal": LEVEL_NORMAL, "score": 0,
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
