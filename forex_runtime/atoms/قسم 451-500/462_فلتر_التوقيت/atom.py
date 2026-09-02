from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "1.1.1"

EVENT_IN = "analysis.session.state"
EVENT_OUT = "decision.filter.timing.state"

METHOD = "timing_gate"
ID_FILTER = "timing_filter"

SIGNAL_PASS = "pass"
SIGNAL_BLOCK = "block"

STATUS_OK = "ok"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"

SESSION_CLOSED = "closed"


def _cycle_id(payload: dict[str, Any], symbol: str) -> str:
    cid = payload.get("cycle_id")
    if cid:
        return str(cid)
    timeframe = str(payload.get("timeframe", ""))
    period_start = payload.get("period_start", payload.get("timestamp", ""))
    return cycle_key_of(payload, symbol=symbol, timeframe=timeframe, period_start=period_start)


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._seen = 0
        self._blocked = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_input)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_input(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        self._seen += 1
        src = str(payload.get("signal", ""))
        passed = src != SESSION_CLOSED
        if not passed:
            self._blocked += 1
        timeframe = str(payload.get("timeframe", ""))
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_FILTER, "cycle_id": _cycle_id(payload, symbol),
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": SIGNAL_PASS if passed else SIGNAL_BLOCK,
            "score": 0, "confidence": 1.0 if passed else 0.0,
            "quality": QUALITY_GOOD if passed else QUALITY_LOW, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe, "passed": passed,
                         "source": src}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d blocked=%d emitted=%d" % (
                self._seen, self._blocked, self._emitted),
            details={"seen": self._seen, "blocked": self._blocked,
                     "emitted": self._emitted})
