from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.analysis_speed import limits_factor
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.0"

EVENT_IN = "structure.trend.state"
EVENT_OUT = "structure.phase.state"

METHOD = "confirmation_maturity"
ID_PHASE = "phase"

TREND_UP = "uptrend"
TREND_DOWN = "downtrend"

PHASE_NEUTRAL = "neutral"
PHASE_EARLY = "early"
PHASE_ESTABLISHED = "established"
PHASE_EXTENDED = "extended"

STATUS_OK = "ok"
QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_TREND_INPUT_YET"

_ESTABLISHED_MAX = 4
_CONFIRM_SCORE = 20.0
_SCORE_MAX = 100.0
_CONF_EARLY = 0.3
_CONF_ESTABLISHED = 0.6
_CONF_EXTENDED = 1.0


@section_atom("200", "208")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._inputs_seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_trend)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_trend(self, payload: dict[str, Any]) -> None:
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
        trend = payload.get("signal")
        try:
            confirms = int(meta_in.get("confirmations", 0))
        except (TypeError, ValueError):
            confirms = 0
        # مفتاح الحدود يقود نضج الطور: أشدّ = تأكيدات أكثر قبل «مؤسَّس/ممتد».
        # عند 50 = ×1.0 فيبقى سقف المانيفست (4) حرفيًّا. الحدث بلا حساب،
        # فالحدود العامة/الرئيسي هما الساريان (معلَن).
        established_max = max(1, round(_ESTABLISHED_MAX * limits_factor("", symbol)))
        phase = self._phase(trend, confirms, established_max)
        await self._emit(symbol, timeframe, cycle_id, phase, confirms)

    def _phase(self, trend: Any, confirms: int, established_max: int) -> str:
        if trend != TREND_UP and trend != TREND_DOWN:
            return PHASE_NEUTRAL
        if confirms <= 0:
            return PHASE_NEUTRAL
        if confirms == 1:
            return PHASE_EARLY
        if confirms <= established_max:
            return PHASE_ESTABLISHED
        return PHASE_EXTENDED

    async def _emit(self, symbol: str, timeframe: str, cycle_id: str,
                    phase: str, confirms: int) -> None:
        if self._context is None:
            return
        if phase == PHASE_EARLY:
            confidence = _CONF_EARLY
        elif phase == PHASE_ESTABLISHED:
            confidence = _CONF_ESTABLISHED
        elif phase == PHASE_EXTENDED:
            confidence = _CONF_EXTENDED
        else:
            confidence = 0.0
        score = int(min(_SCORE_MAX, confirms * _CONFIRM_SCORE))
        meta = {"method": METHOD, "timeframe": timeframe, "confirmations": confirms}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_PHASE, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": phase, "score": score,
            "confidence": confidence, "quality": QUALITY_GOOD, "warnings": [],
            "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._inputs_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="inputs=%d emitted=%d" % (self._inputs_seen, self._emitted),
            details={"inputs": self._inputs_seen, "emitted": self._emitted})
