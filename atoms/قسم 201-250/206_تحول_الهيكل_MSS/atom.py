from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.1.0"

EVENT_BOS = "structure.bos.state"
EVENT_CHOCH = "structure.choch.state"
EVENT_OUT = "structure.mss.state"

METHOD = "shift_unifier"
ID_MSS = "mss"

SIGNAL_BOS = "bos"
SIGNAL_CHOCH = "choch"
SIGNAL_SHIFT = "shift"
SIGNAL_NONE = "none"

SHIFT_BOS = "bos"
SHIFT_CHOCH = "choch"

STATUS_OK = "ok"
QUALITY_GOOD = "good"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_BREAK_INPUT_YET"

_RECENT_CAP = 256


@section_atom("200", "206")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._shifted: deque = deque(maxlen=_RECENT_CAP)
        self._inputs_seen = 0
        self._shifts = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_BOS, self._on_bos)
        context.subscribe(EVENT_CHOCH, self._on_choch)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_bos(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._inputs_seen += 1
        if payload.get("signal") == SIGNAL_BOS:
            await self._shift(payload, SHIFT_BOS)
            return
        cycle_id = str(payload.get("cycle_id", ""))
        if cycle_id not in self._shifted:
            await self._emit_none(payload)

    async def _on_choch(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._inputs_seen += 1
        if payload.get("signal") == SIGNAL_CHOCH:
            await self._shift(payload, SHIFT_CHOCH)

    async def _shift(self, payload: dict[str, Any], shift_type: str) -> None:
        if self._context is None:
            return
        cycle_id = str(payload.get("cycle_id", ""))
        symbol = str(payload.get("symbol", ""))
        meta_in = payload.get("metadata") or {}
        timeframe = str(payload.get("timeframe", "") or meta_in.get("timeframe", ""))
        if cycle_id and cycle_id not in self._shifted:
            self._shifted.append(cycle_id)
        self._shifts += 1
        meta = {"method": METHOD, "timeframe": timeframe,
                "shift_type": shift_type, "direction": meta_in.get("direction"),
                "level": meta_in.get("level")}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_MSS, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": SIGNAL_SHIFT,
            "score": int(payload.get("score", 0)), "confidence": 1.0,
            "quality": QUALITY_GOOD, "warnings": [], "metadata": meta})
        self._emitted += 1

    async def _emit_none(self, payload: dict[str, Any]) -> None:
        if self._context is None:
            return
        symbol = str(payload.get("symbol", ""))
        cycle_id = str(payload.get("cycle_id", ""))
        meta_in = payload.get("metadata") or {}
        timeframe = str(payload.get("timeframe", "") or meta_in.get("timeframe", ""))
        meta = {"method": METHOD, "timeframe": timeframe,
                "shift_type": None, "direction": None, "level": None}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_MSS, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "status": STATUS_OK, "signal": SIGNAL_NONE, "score": 0,
            "confidence": 0.0, "quality": QUALITY_GOOD, "warnings": [], "metadata": meta})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._inputs_seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="inputs=%d shifts=%d emitted=%d" % (
                self._inputs_seen, self._shifts, self._emitted),
            details={"inputs": self._inputs_seen, "shifts": self._shifts,
                     "emitted": self._emitted})
