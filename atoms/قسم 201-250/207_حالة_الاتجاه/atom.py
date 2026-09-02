from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"

EVENT_MSS = "structure.mss.state"
EVENT_TREND151 = "analysis.trend.state"
EVENT_OUT = "structure.trend.state"

METHOD = "mss_governed"
ID_TREND = "structure_trend"

TREND_UP = "uptrend"
TREND_DOWN = "downtrend"
TREND_RANGE = "range"
TREND_TRANS = "transition"

SIGNAL_SHIFT = "shift"
SHIFT_BOS = "bos"
SHIFT_CHOCH = "choch"

DIR_UP = "up"

SRC_MSS = "mss"
SRC_DEFAULT = "trend_151"

STATUS_OK = "ok"
QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_STRUCTURE_INPUT_YET"

_MAP151 = {"up": TREND_UP, "down": TREND_DOWN, "sideways": TREND_RANGE}
_CONF_DIR = 1.0
_CONF_TENTATIVE = 0.5
_CONFIRM_SCORE = 20.0
_SCORE_MAX = 100.0


@section_atom("200", "207")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._state: dict[tuple, dict[str, Any]] = {}
        self._mss_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_MSS, self._on_mss)
        context.subscribe(EVENT_TREND151, self._on_trend151)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _get(self, key: tuple) -> dict[str, Any]:
        st = self._state.get(key)
        if st is None:
            st = {"official": TREND_RANGE, "has_mss": False,
                  "default": TREND_RANGE, "dir": None, "confirms": 0}
            self._state[key] = st
        return st

    async def _on_trend151(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        meta = payload.get("metadata") or {}
        key = (str(symbol), str(meta.get("timeframe", "")))
        st = self._get(key)
        mapped = _MAP151.get(payload.get("signal"), TREND_RANGE)
        st["default"] = mapped
        if not st["has_mss"]:
            st["official"] = mapped

    async def _on_mss(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        self._mss_seen += 1
        cycle_id = str(payload.get("cycle_id", ""))
        meta_in = payload.get("metadata") or {}
        timeframe = str(payload.get("timeframe", "") or meta_in.get("timeframe", ""))
        st = self._get((symbol, timeframe))
        if payload.get("signal") == SIGNAL_SHIFT:
            st["has_mss"] = True
            shift_type = meta_in.get("shift_type")
            if shift_type == SHIFT_BOS:
                new_dir = TREND_UP if meta_in.get("direction") == DIR_UP else TREND_DOWN
                if new_dir == st["dir"]:
                    st["confirms"] += 1
                else:
                    st["dir"] = new_dir
                    st["confirms"] = 1
                st["official"] = new_dir
            elif shift_type == SHIFT_CHOCH:
                st["official"] = TREND_TRANS
                st["confirms"] = 0
        elif not st["has_mss"]:
            st["official"] = st["default"]
        source = SRC_MSS if st["has_mss"] else SRC_DEFAULT
        await self._emit(symbol, timeframe, cycle_id, st, source)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    st: dict[str, Any], source: str) -> None:
        if self._context is None:
            return
        official = st["official"]
        if official == TREND_UP or official == TREND_DOWN:
            confidence = _CONF_DIR if st["has_mss"] else _CONF_TENTATIVE
            score = int(min(_SCORE_MAX, st["confirms"] * _CONFIRM_SCORE))
        elif official == TREND_TRANS:
            confidence = _CONF_TENTATIVE
            score = 0
        else:
            confidence = 0.0
            score = 0
        meta = {"method": METHOD, "timeframe": timeframe,
                "confirmations": st["confirms"], "source": source}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_TREND, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": official, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._mss_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details={"tracked": len(self._state)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="mss=%d emitted=%d tracked=%d" % (
                self._mss_seen, self._emitted, len(self._state)),
            details={"mss": self._mss_seen, "emitted": self._emitted,
                     "tracked": len(self._state)})
