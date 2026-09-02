from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_DECISION = "trading.final_decision"
EVENT_MANAGE = "execution.manage.command"
EVENT_EXTRACT = "asset.extraction.requested"
EVENT_FULL = "asset.full_extraction.requested"
EVENT_WRITTEN = "platform.brain_signal.written"
EVENT_TRADE = "platform.trade_event"
EVENT_PORTFOLIO = "asset.portfolio.state"
EVENT_ESCALATION = "perpetual.owner.escalation"
EVENT_OUT = "audit.trail.state"

REASON_NOT_STARTED = "NOT_STARTED"

_KEY_SEP = "|"
_PUBLISH_TAIL = 50
_FIELDS = ("account_id", "symbol", "side", "volume", "action", "ticket",
           "stop_loss", "amount", "milestone", "origin", "request_id",
           "state", "prev_state", "protection_intent", "reason",
           "event_type", "profit", "target")


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _FIELDS:
        if field in payload and payload[field] is not None:
            out[field] = payload[field]
    return out


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._log_path = ""
        self._max_memory = 100
        self._recent: list[dict[str, Any]] = []
        self._seq = 0
        self._write_failures = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._log_path = str(cfg.get("log_path", os.path.join("var", "audit", "perpetual.jsonl")))
        self._max_memory = int(cfg.get("max_memory", 100))
        parent = os.path.dirname(self._log_path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                context.logger.warning("522 makedirs failed: %s", exc)
        context.subscribe(EVENT_DECISION, self._on_decision)
        context.subscribe(EVENT_MANAGE, self._on_manage)
        context.subscribe(EVENT_EXTRACT, self._on_extract)
        context.subscribe(EVENT_FULL, self._on_full)
        context.subscribe(EVENT_WRITTEN, self._on_written)
        context.subscribe(EVENT_TRADE, self._on_trade)
        context.subscribe(EVENT_ESCALATION, self._on_escalation)
        context.subscribe(EVENT_PORTFOLIO, self._on_portfolio)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _append_disk(self, line: str) -> None:
        with open(self._log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    async def _record(self, kind: str, payload: dict[str, Any]) -> None:
        if self._context is None:
            return
        self._seq += 1
        entry: dict[str, Any] = {"seq": self._seq, "kind": kind}
        entry.update(_summary(payload))
        stamp = payload.get("timestamp")
        if stamp is not None:
            entry["ts"] = stamp
        self._recent.append(entry)
        if len(self._recent) > self._max_memory:
            self._recent = self._recent[-self._max_memory:]
        try:
            await asyncio.to_thread(self._append_disk, json.dumps(entry, ensure_ascii=False))
        except OSError as exc:
            self._write_failures += 1
            self._context.logger.warning("522 audit write failed: %s", exc)
        tail = min(self._max_memory, _PUBLISH_TAIL)
        await self._context.publish(EVENT_OUT, {"recent": self._recent[-tail:], "total": self._seq})

    async def _on_decision(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            await self._record("order", payload)

    async def _on_manage(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            await self._record("manage", payload)

    async def _on_extract(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            await self._record("extract", payload)

    async def _on_full(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            await self._record("full_extract", payload)

    async def _on_written(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            await self._record("written", payload)

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            await self._record("trade_event", payload)

    async def _on_escalation(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            await self._record("owner_escalation", payload)

    async def _on_portfolio(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        portfolios = payload.get("portfolios")
        if not isinstance(portfolios, list):
            return
        for pf in portfolios:
            if isinstance(pf, dict) and pf.get("changed"):
                await self._record("state_change", pf)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"recorded": self._seq, "write_failures": self._write_failures,
                   "log_path": self._log_path}
        if self._write_failures > 0 and self._seq == 0:
            return HealthStatus(state=HealthState.DEGRADED, message="audit disk unwritable",
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="recorded=%d" % self._seq, details=details)
