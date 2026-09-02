from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.durable_execution_journal import Journal
from shared.financial_scope import row_key, text

ATOM_VERSION = "3.1.1"
# v3.1.1 (2026-08-27, item 24/27 of the 27-atom review -- blocking I/O at
# boot + restore() without guarding): initialize() called
# self._journal.ensure() (mkdir + open a raw sqlite connection + run
# schema statements + commit -- all synchronous) directly on the event
# loop, unlike every OTHER Journal call in this same atom
# (remember_request/commit_event/pending_outputs/mark_emitted/counts),
# which already run via asyncio.to_thread. Now consistent. restore()'s
# top-level guard was already fine, but the four counters were
# int(state.get(...) or 0) with no try/except -- a corrupted non-numeric
# value (e.g. a string) raised a raw, uncontrolled ValueError instead of
# the same clean INVALID_EXECUTION_CONFIRM_STATE the top-level check
# already uses, and a failure partway through left self torn (earlier
# counters already committed). Both fixed.
EVENT_IN = "platform.trade_event"
EVENT_OUT = "market.outcome.realized"
EVENT_ACK = "execution.command.ack"
EVENT_REJECTED = "execution.confirmation.rejected"
EVENT_FINAL = "trading.final_decision"
EVENT_SPECS = "market.symbol_specs"
EVENT_ACCOUNT = "platform.account.state"
SIDE_BUY = "BUY"
_PRICE_DP = 6
EVT_OPENED = "OPENED"
EVT_CLOSED = "CLOSED"
EVT_PARTIAL = "PARTIAL"
# NQ seal, item 22, package T (T1): broker events cannot carry the decision
# identity (the external bridge never sees those fields as columns), so it is
# rejoined HERE from the durable request ledger this atom already keeps -- the
# remembered trading.final_decision payload, safe across any reboot. Absent
# values pass as None and are declared, never invented.
IDENTITY_FIELDS = ("decision_id", "gate_request_id")
WARNING_IDENTITY = "identity_incomplete"


def number(value: Any) -> float | None:
    if isinstance(value, bool): return None
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context = None; self._running = False
        self._brokers: dict[str, str] = {}
        self._specs: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._pending_specs: list[dict[str, Any]] = []
        self._journal: Journal | None = None
        self._journal_ready = False; self._last_error = ""
        self._seen = 0
        self._opened = 0
        self._realized = 0
        self._dropped = 0
        self._duplicates = 0
        self._missing_identity = 0
        self._measured = 0
        self._unmatched = 0
        self._outbox_recovered = 0
        self._identity_incomplete = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._journal = Journal(str(context.config.get(
            "dedupe_db_path", "var/store/execution_confirmation.db")))
        try:
            await asyncio.to_thread(self._journal.ensure); self._journal_ready = True
        except (OSError, sqlite3.Error) as exc:
            self._last_error = str(exc); self._journal_ready = False
        context.subscribe(EVENT_IN, self._on_event)
        context.subscribe(EVENT_FINAL, self._on_requested)
        context.subscribe(EVENT_SPECS, self._on_specs)
        context.subscribe(EVENT_ACCOUNT, self._on_account)

    async def start(self) -> None:
        self._running = True
        if self._journal_ready:
            self._outbox_recovered = await self._drain_outbox()

    async def stop(self) -> None: self._running = False
    async def shutdown(self) -> None: await self.stop()

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account = text(payload.get("account_id")); broker = text(payload.get("broker"))
        if account and broker:
            self._brokers[account] = broker
            pending, self._pending_specs = self._pending_specs, []
            for item in pending: await self._on_specs(item)

    async def _on_specs(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        for row in payload.get("symbols", []) if isinstance(payload.get("symbols"), list) else []:
            if not isinstance(row, dict): continue
            key = row_key(payload, row, self._brokers)
            if key is not None:
                spec = dict(row)
                spec["point"] = number(row.get("point")) or number(row.get("tick_size"))
                spec["spec_digest"] = hashlib.sha256(json.dumps(
                    spec, sort_keys=True, default=str).encode()).hexdigest()
                self._specs[key] = spec
            elif text(row.get("account_id") or payload.get("account_id")) \
                    and payload not in self._pending_specs:
                self._pending_specs.append(dict(payload))

    async def _on_requested(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict) or not self._journal_ready:
            return
        account = text(payload.get("account_id")); broker = text(payload.get("broker")) \
            or self._brokers.get(account, "")
        request_id = text(payload.get("request_id")); symbol = text(payload.get("symbol"))
        requested = number(payload.get("reference_price"))
        if not account or not broker or not request_id or not symbol or requested is None:
            return
        spec = self._specs.get((account, broker, symbol), {})
        row = {"account_id": account, "request_id": request_id, "broker": broker,
               "symbol": symbol, "side": text(payload.get("side")).upper(),
               "requested_price": requested, "point": number(spec.get("point")),
               "tick_value": number(spec.get("tick_value")),
               "tick_size": number(spec.get("tick_size")),
               "spec_digest": spec.get("spec_digest"), "payload": dict(payload)}
        try:
            await asyncio.to_thread(self._journal.remember_request, row)
        except (OSError, sqlite3.Error) as exc:
            self._journal_ready = False; self._last_error = str(exc)

    async def _publish_rejection(self, payload: dict[str, Any], reason: str) -> None:
        self._dropped += 1
        if self._context is not None:
            await self._context.publish(EVENT_REJECTED, {**payload, "reason": reason})

    async def _on_event(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        if not self._journal_ready or self._journal is None:
            await self._publish_rejection(payload, "DURABLE_DEDUPE_UNAVAILABLE"); return
        event_type = text(payload.get("event_type")).upper()
        if event_type not in {EVT_OPENED, EVT_CLOSED, EVT_PARTIAL}: return
        account = text(payload.get("account_id")); broker = text(payload.get("broker")) \
            or self._brokers.get(account, "")
        source_identity = text(payload.get("source_row_id") or payload.get("event_id")
                               or payload.get("deal_id"))
        if not account or not broker or not source_identity:
            self._missing_identity += 1
            await self._publish_rejection(payload, "MISSING_DURABLE_EVENT_ID_OR_SCOPE"); return
        identity = "|".join((account, broker, event_type, source_identity))
        if event_type in {EVT_CLOSED, EVT_PARTIAL}:
            revision = {name: payload.get(name) for name in
                        ("profit", "commission", "swap", "fee", "trade_id", "request_id")}
            revision_id = hashlib.sha256(json.dumps(
                revision, sort_keys=True, default=str).encode()).hexdigest()[:24]
            identity += "|" + revision_id
        outputs = await self._outputs(identity, event_type, account, broker, payload)
        try:
            inserted = await asyncio.to_thread(
                self._journal.commit_event, identity, account, event_type,
                source_identity, payload, outputs)
        except (OSError, sqlite3.Error) as exc:
            self._journal_ready = False; self._last_error = str(exc)
            await self._publish_rejection(payload, "DURABLE_DEDUPE_UNAVAILABLE"); return
        if not inserted:
            self._duplicates += 1; self._dropped += 1; return
        self._seen += 1
        if event_type == EVT_OPENED: self._opened += 1
        else: self._realized += 1
        await self._drain_outbox()

    def _decision_identity(self, requested: dict[str, Any] | None,
                           payload: dict[str, Any]) -> dict[str, Any]:
        """T1: the identity pair for a result event -- the broker payload first
        (future-proof), then the durably remembered request. Missing values
        pass as None and are declared."""
        source = (requested or {}).get("payload")
        source = source if isinstance(source, dict) else {}
        body: dict[str, Any] = {}
        missing = []
        for field in IDENTITY_FIELDS:
            value = payload.get(field) or source.get(field)
            body[field] = value
            if not value:
                missing.append(field)
        if missing:
            self._identity_incomplete += 1
            body["identity_missing"] = missing
            body["identity_warnings"] = [WARNING_IDENTITY]
        return body

    async def _outputs(self, identity: str, event_type: str, account: str, broker: str,
                       payload: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
        if self._journal is None: return []
        request_id = text(payload.get("request_id"))
        requested = await asyncio.to_thread(self._journal.request, account, request_id) \
            if request_id else None
        if event_type == EVT_OPENED:
            slippage = self._slippage(requested, number(payload.get("entry_price")),
                                      number(payload.get("volume")))
            body = {"command_id": request_id or text(payload.get("ticket")),
                    "request_id": request_id, "status": "ACK", "account_id": account,
                    "broker": broker, "symbol": text(payload.get("symbol")),
                    "ticket": payload.get("ticket"), "side": text(payload.get("side")),
                    "volume": number(payload.get("volume")),
                    "entry_price": number(payload.get("entry_price")),
                    "pair_id": payload.get("pair_id"), "leg_role": payload.get("leg_role"),
                    **self._decision_identity(requested, payload), **slippage}
            return [("execution-confirm:" + identity + ":ack", EVENT_ACK, body)]
        profit = number(payload.get("profit")); symbol = text(payload.get("symbol"))
        if profit is None or not symbol:
            return [("execution-confirm:" + identity + ":rejected", EVENT_REJECTED,
                     {**payload, "account_id": account, "broker": broker,
                      "reason": "MISSING_REALIZED_PROFIT_OR_SYMBOL"})]
        result = "WIN" if profit > 0 else "LOSS" if profit < 0 else "BREAKEVEN"
        trade_identity = text(payload.get("trade_id") or payload.get("order_id") or
                              request_id or payload.get("ticket") or payload.get("source_row_id"))
        body = {"id": "execution_confirm", "symbol": symbol, "profit": round(profit, 2),
                "account_id": account, "broker": broker, "request_id": request_id,
                "trade_identity": trade_identity,
                "result": result, "side": text(payload.get("side")),
                "volume": number(payload.get("volume")),
                "entry_price": number(payload.get("entry_price")),
                "exit_price": number(payload.get("exit_price")),
                "event_type": event_type, "reason": text(payload.get("reason")),
                "ticket": payload.get("ticket"), "trade_id": payload.get("trade_id"),
                "source_row_id": payload.get("source_row_id"),
                "commission": number(payload.get("commission")),
                "swap": number(payload.get("swap")), "fee": number(payload.get("fee")),
                **self._decision_identity(requested, payload)}
        return [("execution-confirm:" + identity + ":outcome", EVENT_OUT, body)]

    def _slippage(self, requested: dict[str, Any] | None, filled: float | None,
                  volume: float | None) -> dict[str, Any]:
        if requested is None or filled is None:
            self._unmatched += 1
            return {"slippage_measured": False, "slippage_usable": False,
                    "slippage_reason": "NO_REQUESTED_PRICE",
                    "slippage_blocks_decisions": True}
        point = number(requested.get("point")); asked = number(requested.get("requested_price"))
        if point is None or point <= 0 or asked is None:
            self._unmatched += 1
            return {"slippage_measured": False, "slippage_usable": False,
                    "slippage_reason": "NO_SCOPED_POINT_SPEC",
                    "slippage_blocks_decisions": True,
                    "requested_price": asked, "executed_price": filled}
        adverse = (filled - asked) * (1.0 if requested.get("side") == SIDE_BUY else -1.0)
        tick_value = number(requested.get("tick_value")); tick_size = number(requested.get("tick_size"))
        vpu = tick_value / tick_size if tick_value and tick_size else None
        self._measured += 1
        return {"slippage_measured": True, "slippage_usable": True,
                "slippage_blocks_decisions": False,
                "requested_price": round(asked, _PRICE_DP),
                "executed_price": round(filled, _PRICE_DP),
                "slippage_price": round(adverse, _PRICE_DP),
                "slippage_points": round(adverse / point, 1),
                "slippage_cost": round(adverse * vpu * volume, 2) if vpu and volume else None,
                "slippage_adverse": adverse > 0, "spec_digest": requested.get("spec_digest")}

    async def _drain_outbox(self) -> int:
        if self._journal is None or self._context is None: return 0
        emitted = 0
        while True:
            try:
                rows = await asyncio.to_thread(self._journal.pending_outputs)
            except (OSError, sqlite3.Error) as exc:
                self._journal_ready = False; self._last_error = str(exc); return emitted
            if not rows: return emitted
            for output_id, event_name, payload in rows:
                await self._context.publish(event_name, payload)
                await asyncio.to_thread(self._journal.mark_emitted, output_id)
                emitted += 1

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "journal_path": self._journal.path if self._journal else None,
                "seen": self._seen, "opened": self._opened, "realized": self._realized,
                "duplicates": self._duplicates}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict): raise ValueError("INVALID_EXECUTION_CONFIRM_STATE")
        try:
            new_seen = int(state.get("seen") or 0)
            new_opened = int(state.get("opened") or 0)
            new_realized = int(state.get("realized") or 0)
            new_duplicates = int(state.get("duplicates") or 0)
        except (TypeError, ValueError):
            raise ValueError("INVALID_EXECUTION_CONFIRM_STATE")
        self._seen = new_seen; self._opened = new_opened
        self._realized = new_realized; self._duplicates = new_duplicates

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        journal_counts = {}
        if self._journal_ready and self._journal is not None:
            try: journal_counts = await asyncio.to_thread(self._journal.counts)
            except (OSError, sqlite3.Error) as exc:
                self._journal_ready = False; self._last_error = str(exc)
        details = {"seen": self._seen, "opened": self._opened, "realized": self._realized,
                   "dropped": self._dropped, "duplicates": self._duplicates,
                   "missing_identity": self._missing_identity, "slippage_measured": self._measured,
                   "slippage_unmatched": self._unmatched, "journal_ready": self._journal_ready,
                   "journal": journal_counts, "outbox_recovered": self._outbox_recovered,
                   "identity_incomplete": self._identity_incomplete,
                   "last_error": self._last_error}
        if not self._journal_ready:
            return HealthStatus(state=HealthState.UNHEALTHY,
                                message="DURABLE_DEDUPE_UNAVAILABLE", details=details)
        if self._missing_identity or self._unmatched or journal_counts.get("outbox_pending"):
            return HealthStatus(state=HealthState.DEGRADED,
                                message="CONFIRMATION_INCOMPLETE", details=details)
        if not self._seen:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_PLATFORM_TRADE_EVENT | processed=0 duplicates=0",
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="processed=%d duplicates=%d" % (self._seen, self._duplicates),
                            details=details)
