from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.atom_evidence import window_evidence
from shared.cycle_identity import cycle_key_of

WEIGHT = 5.882353
ATOM_VERSION = "1.2.0"

EVENT_IN = "market.tick.validated"
EVENT_OUT = "stats.quality.state"

METHOD = "rolling_data_quality"
ID_QUALITY = "quality"

SIGNAL_CLEAN = "clean"
SIGNAL_DEGRADED = "degraded"

STATUS_OK = "ok"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

WARN_DEGRADED = "data_quality_degraded"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TICKS = "NO_TICKS_YET"

_PERCENT = 100.0
_DP = 6


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


@section_atom("300", "316")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window_size = 20
        self._min_quality = 0.95
        self._state: dict[tuple, dict[str, Any]] = {}
        self._ticks_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._window_size = int(context.config["window_size"])
        self._min_quality = float(context.config["min_quality"])
        context.subscribe(EVENT_IN, self._on_tick)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _new_state(self) -> dict[str, Any]:
        return {"flags": deque(maxlen=self._window_size), "last_ps": None,
                "received": 0, "nan": 0, "nonpos": 0, "dup": 0, "ooo": 0}

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        timeframe = "tick"
        period_start = str(payload.get("sequence") or "")
        cycle_id = cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)
        key = (symbol, timeframe)
        st = self._state.get(key)
        if st is None:
            st = self._new_state()
            self._state[key] = st
        st["received"] += 1
        self._ticks_seen += 1
        price = _to_float(payload.get("price"))
        if price is None:
            st["nan"] += 1
            valid = 0
        elif price <= 0.0:
            st["nonpos"] += 1
            valid = 0
        else:
            valid = 1
        last_ps = st["last_ps"]
        if last_ps is not None and period_start == last_ps:
            st["dup"] += 1
        ps_num = _to_float(period_start)
        last_num = _to_float(last_ps) if last_ps is not None else None
        if ps_num is not None and last_num is not None and ps_num < last_num:
            st["ooo"] += 1
        st["last_ps"] = period_start
        st["flags"].append(valid)
        await self._emit(symbol, timeframe, cycle_id, st)

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    st: dict[str, Any]) -> None:
        if self._context is None:
            return
        flags = st["flags"]
        sampled = len(flags)
        valid = sum(flags)
        ratio = valid / sampled if sampled > 0 else 0.0
        clean = ratio >= self._min_quality
        signal = SIGNAL_CLEAN if clean else SIGNAL_DEGRADED
        confidence = round(min(1.0, sampled / self._window_size), 2) \
            if self._window_size > 0 else 0.0
        meta = {"method": METHOD, "timeframe": timeframe, "window": self._window_size,
                "received": st["received"], "valid": valid, "sampled": sampled,
                "rejected": st["nan"] + st["nonpos"], "nan": st["nan"],
                "nonpositive": st["nonpos"], "duplicates": st["dup"],
                "out_of_order": st["ooo"], "quality_ratio": round(ratio, _DP)}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_QUALITY, "cycle_id": cycle_id,
            "timeframe": timeframe,
            **window_evidence(have=sampled, need=self._window_size),
            "status": STATUS_OK, "signal": signal,
            "score": int(round(ratio * _PERCENT)), "confidence": min(100.0, confidence * 100.0),
            "quality": QUALITY_GOOD if clean else QUALITY_LOW, "weight": WEIGHT,
            "analysis_state": "READY", "ready": True,
            "warnings": [] if clean else [WARN_DEGRADED], "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._ticks_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_TICKS,
                                details={"tracked": len(self._state)})
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="ticks=%d emitted=%d tracked=%d" % (
                self._ticks_seen, self._emitted, len(self._state)),
            details={"ticks": self._ticks_seen, "emitted": self._emitted,
                     "tracked": len(self._state)})
