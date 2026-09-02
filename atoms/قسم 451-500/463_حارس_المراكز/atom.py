from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

from shared.decision_dials import (EVENT_COMMAND as EVENT_DIALS_COMMAND,
                                   EVENT_STATE as EVENT_DIALS_STATE,
                                   apply_command, effective_value)

ATOM_VERSION = "1.2.1"

EVENT_IN = "platform.positions.state"
EVENT_SCORED = "decision.scored.state"
EVENT_RESOLVED = "decision.resolved.state"
EVENT_COMMAND = "risk.asset.command"
EVENT_OUT = "decision.filter.position.state"

METHOD = "one_position_per_symbol"
ID_FILTER = "position_filter"

SIGNAL_PASS = "pass"
SIGNAL_BLOCK = "block"

STATUS_OK = "ok"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"


class Atom(AtomBase):
    def __init__(self) -> None:
        self._dropped = 0
        self._context: AtomContext | None = None
        self._running = False
        self._max_per_symbol = 1
        self._blocked: set[str] = set()
        self._asked: set[str] = set()
        self._counts: dict[str, dict[str, int]] = {}
        self._dials_applied = 0
        self._seen = 0
        self._emitted = 0
        self._verdicts = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._max_per_symbol = int(effective_value(
            "DECISION_MAX_PER_SYMBOL", float(context.config["max_per_symbol"])))
        self._dials_applied = 0
        context.subscribe(EVENT_DIALS_COMMAND, self._on_dial_command)
        context.subscribe(EVENT_IN, self._on_positions)
        context.subscribe(EVENT_SCORED, self._on_resolved)
        context.subscribe(EVENT_RESOLVED, self._on_resolved)
        context.subscribe(EVENT_COMMAND, self._on_command)

    def _symbol_count(self, symbol: str) -> int:
        return sum(counts.get(symbol, 0) for counts in self._counts.values())

    def _known_symbols(self) -> set[str]:
        symbols: set[str] = set()
        for counts in self._counts.values():
            symbols.update(counts)
        return symbols

    async def _on_command(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            self._dropped += 1
            return
        if str(payload.get("command") or "").upper() != "SET_MAX_PER_SYMBOL":
            return
        try:
            limit = int(float(payload["max_per_symbol"]))
        except (KeyError, TypeError, ValueError):
            return
        if limit >= 1:
            self._max_per_symbol = limit

    async def _on_dial_command(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        applied = apply_command(payload, atom_id="463")
        if applied is None:
            return
        self._max_per_symbol = max(1, int(applied["value"]))
        self._dials_applied += 1
        await self._publish_dials_state()

    async def _publish_dials_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_DIALS_STATE, {
            "id": "decision_dials_463", "atom_id": "463", "status": STATUS_OK,
            "dials": {"DECISION_MAX_PER_SYMBOL": float(self._max_per_symbol)}})

    async def start(self) -> None:
        self._running = True
        await self._publish_dials_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_positions(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        by_symbol = payload.get("by_symbol")
        if not isinstance(by_symbol, dict):
            rows = payload.get("positions")
            if not isinstance(rows, list):
                return
            by_symbol = {}
            for row in rows:
                if isinstance(row, dict):
                    sym = str(row.get("symbol") or "")
                    if sym:
                        by_symbol[sym] = by_symbol.get(sym, 0) + 1
        account = str(payload.get("account_id") or "*")
        self._seen += 1
        self._counts[account] = {
            str(symbol): int(count) for symbol, count in by_symbol.items()
            if isinstance(count, (int, float)) and int(count) > 0
        }
        held = {symbol for symbol in self._known_symbols()
                if self._symbol_count(symbol) >= self._max_per_symbol}
        freed = self._blocked - held
        for symbol in sorted(held):
            await self._emit(symbol, False)
        for symbol in sorted(freed):
            await self._emit(symbol, True)
        self._blocked = held
        for symbol in sorted(self._asked - held - freed):
            await self._emit(symbol, self._symbol_count(symbol) < self._max_per_symbol)

    async def _on_resolved(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        if self._seen == 0:
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        cycle_id = str(payload.get("cycle_id") or "")
        self._asked.add(symbol)
        passed = self._symbol_count(symbol) < self._max_per_symbol
        self._verdicts += 1
        await self._emit(symbol, passed, cycle_id)

    async def _emit(self, symbol: str, passed: bool, cycle_id: str = "") -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_FILTER, "cycle_id": cycle_id,
            "status": STATUS_OK, "signal": SIGNAL_PASS if passed else SIGNAL_BLOCK,
            "score": 0, "confidence": 1.0 if passed else 0.0,
            "quality": QUALITY_GOOD if passed else QUALITY_LOW, "warnings": [],
            "metadata": {"method": METHOD, "timeframe": "", "passed": passed,
                         "max_per_symbol": self._max_per_symbol}})
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d blocked=%d verdicts=%d emitted=%d" % (
                self._seen, len(self._blocked), self._verdicts, self._emitted),
            details={"seen": self._seen, "blocked": sorted(self._blocked),
                     "verdicts": self._verdicts, "emitted": self._emitted})
