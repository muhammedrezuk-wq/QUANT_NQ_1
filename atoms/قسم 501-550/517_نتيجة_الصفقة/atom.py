from __future__ import annotations

import asyncio
import hashlib
import math
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_truth import EVENT_SHORTAGE, FinancialTruth, bind_truth
from shared.durable_execution_journal import Journal
from shared.financial_scope import text

ATOM_VERSION = "4.1.0"
EVENT_OUTCOME = "market.outcome.realized"
EVENT_ACCOUNT = "platform.account.state"
EVENT_OUT = "risk.loss_reported"
COSTS = ("commission", "swap", "fee")
# NQ seal, item 22, package T (T1): the decision identity pair rides the
# outcome into the loss report -- merged durably with the rest of the trade
# state, absent values pass as None and are declared, never invented.
IDENTITY_FIELDS = ("decision_id", "gate_request_id")
WARNING_IDENTITY = "identity_incomplete"
CONSUMER = "517"
MIN_TIMEOUT_S = 0.05
WATCHDOG_MAX_SLEEP_S = 0.1
WATCHDOG_MIN_SLEEP_S = 0.01
WATCHDOG_DIVISOR = 4.0


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._brokers: dict[str, str] = {}
        self._truth = FinancialTruth("517")
        self._processed_event_ids: set[str] = set()
        self._pending: dict[str, dict[str, Any]] = {}
        self._deadlines: dict[str, float] = {}
        self._timeout_s = 30.0
        self._journal: Journal | None = None
        self._storage_error = ""
        self._outcomes = 0
        self._emitted = 0
        self._duplicates = 0
        self._dropped_identity = 0
        self._incomplete = 0
        self._completed = 0
        self._identity_incomplete = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeout_s = max(MIN_TIMEOUT_S, float(context.config.get("cost_wait_timeout_s", 30.0)))
        path = str(context.config.get("consumer_db_path") or
                   "var/store/risk_outcome_consumer_517.db")
        self._journal = Journal(path)
        try:
            self._journal.ensure()
            for scope, state in self._journal.consumer_states(CONSUMER).items():
                if state.get("status") == "PARTIAL":
                    self._pending[scope] = state
                    self._deadlines[scope] = clock.mono() + max(
                        0.0, float(state.get("remaining_s", self._timeout_s)))
        except Exception as exc:
            self._storage_error = type(exc).__name__
        context.subscribe(EVENT_OUTCOME, self._on_outcome)
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        bind_truth(self, context, self._truth, ("equity",))

    async def start(self) -> None:
        self._running = True
        await self._flush_outbox()
        self._task = asyncio.create_task(self._watchdog())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def shutdown(self) -> None:
        await self.stop()

    async def _flush_outbox(self) -> None:
        if self._context is None or self._journal is None or self._storage_error:
            return
        try:
            for output_id, event_name, payload in self._journal.pending_outputs():
                await self._context.publish(event_name, payload)
                self._journal.mark_emitted(output_id)
                self._emitted += 1
        except Exception as exc:
            self._storage_error = type(exc).__name__

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = text(payload.get("account_id")); broker = text(payload.get("broker"))
        if account and broker:
            self._brokers[account] = broker
            if not self._truth.has(account, "equity") and self._context is not None:
                await self._context.publish(EVENT_SHORTAGE, self._truth.shortage_body(
                    account, "equity", broker=broker, detail="517 trade outcome"))

    def _scope(self, payload: dict[str, Any]) -> tuple[str, str, str, str] | None:
        account = text(payload.get("account_id"))
        broker = text(payload.get("broker")) or self._brokers.get(account, "")
        trade = text(payload.get("trade_identity") or payload.get("trade_id") or
                     payload.get("order_id") or payload.get("request_id"))
        event_id = text(payload.get("event_id"))
        if not account or not broker or not trade or not event_id:
            return None
        return account, broker, trade, event_id

    def _output(self, state: dict[str, Any], completeness: str) -> dict[str, Any]:
        gross = number(state.get("profit")); account = str(state["account_id"])
        broker = str(state["broker"]); capital = self._truth.get(account, "equity")
        values = {name: number(state.get(name)) for name in COSTS}
        unknown = [name for name, value in values.items() if value is None]
        complete = completeness == "COMPLETE" and gross is not None and capital is not None
        net = gross + sum(value for value in values.values() if value is not None) if complete else None
        loss_pct = -(net/capital)*100.0 if net is not None and capital else None
        identity = {field: state.get(field) for field in IDENTITY_FIELDS}
        identity_missing = [field for field in IDENTITY_FIELDS if not identity[field]]
        if identity_missing:
            self._identity_incomplete += 1
            identity["identity_missing"] = identity_missing
            identity["identity_warnings"] = [WARNING_IDENTITY]
        return {"id": "trade_outcome", "account_id": account, "broker": broker,
                "symbol": state.get("symbol"), "ticket": state.get("ticket"),
                "trade_id": state.get("trade_id"), "trade_identity": state.get("trade_identity"),
                "request_id": state.get("request_id"), **identity, "completeness": completeness,
                "costs_complete": complete, "costs_known": [name for name in COSTS if name not in unknown],
                "costs_unknown": unknown, "gross_pnl": gross,
                "pnl": round(net, 2) if net is not None else None,
                "loss_pct": round(loss_pct, 4) if loss_pct is not None else None,
                "is_loss": net < 0 if net is not None else None,
                "commission": values["commission"], "swap": values["swap"],
                "fee": values["fee"],
                "cost_total": round(net-gross, 2) if net is not None and gross is not None else None,
                "metadata": {"reference_capital": capital}, "timestamp": state.get("timestamp")}

    def _merge(self, state: dict[str, Any], payload: dict[str, Any],
               account: str, broker: str, trade: str, remaining: float):
        if state.get("status") in {"COMPLETE","INCOMPLETE"}:
            return state, []
        state.update({"account_id": account, "broker": broker, "trade_identity": trade})
        for name in ("symbol", "ticket", "trade_id", "request_id", "timestamp", "profit",
                     *IDENTITY_FIELDS, *COSTS):
            if payload.get(name) is not None:
                state[name] = payload.get(name)
        state["remaining_s"] = max(0.0, remaining)
        known = all(number(state.get(name)) is not None for name in ("profit", *COSTS))
        capital = self._truth.get(account, "equity")
        if known and capital is not None and capital > 0:
            state["status"] = "COMPLETE"; state["remaining_s"] = 0.0
            state["terminal_input_id"] = payload.get("event_id")
            output_id = "trade-result:" + _digest(account + "|" + broker + "|" + trade)
            return state, [(output_id, EVENT_OUT, self._output(state, "COMPLETE"))]
        state["status"] = "PARTIAL"
        return state, []

    async def _on_outcome(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self._outcomes += 1
        identity = self._scope(payload)
        if identity is None:
            self._dropped_identity += 1
            return
        if self._journal is None or self._storage_error:
            return
        account, broker, trade, event_id = identity
        scope = account + "|" + broker + "|" + trade
        deadline = self._deadlines.get(scope, clock.mono() + self._timeout_s)
        remaining = max(0.0, deadline-clock.mono())
        initial = {"status": "PARTIAL", "remaining_s": self._timeout_s,
                   "account_id": account, "broker": broker, "trade_identity": trade}
        try:
            fresh, state = self._journal.reduce_consumer_event(
                event_id, account, "OUTCOME_REVISION", trade, payload, CONSUMER,
                scope, initial,
                lambda current: self._merge(current, payload, account, broker, trade, remaining))
        except Exception as exc:
            self._storage_error = type(exc).__name__
            return
        if not fresh:
            self._duplicates += 1
            return
        self._processed_event_ids.add(event_id)
        if state.get("status") in {"COMPLETE","INCOMPLETE"}:
            self._pending.pop(scope, None); self._deadlines.pop(scope, None)
            if state.get("status")=="COMPLETE" and state.get("terminal_input_id")==event_id:self._completed += 1
        else:
            self._pending[scope] = state; self._deadlines[scope] = deadline
        await self._flush_outbox()

    async def _expire(self, scope: str) -> None:
        if self._journal is None or scope not in self._pending:
            return
        state = self._pending[scope]; account = str(state.get("account_id") or "")
        timeout_id = "outcome-timeout:" + _digest(scope)
        def reduce(current):
            if current.get("status") != "PARTIAL":
                return current, []
            current["status"] = "INCOMPLETE"; current["remaining_s"] = 0.0
            output_id = "trade-result:" + _digest(scope)
            return current, [(output_id, EVENT_OUT, self._output(current, "INCOMPLETE"))]
        try:
            fresh, final = self._journal.reduce_consumer_event(
                timeout_id, account, "OUTCOME_TIMEOUT", scope, state, CONSUMER,
                scope, state, reduce)
        except Exception as exc:
            self._storage_error = type(exc).__name__
            return
        self._pending.pop(scope, None); self._deadlines.pop(scope, None)
        if fresh:
            self._incomplete += 1
        await self._flush_outbox()

    async def _watchdog(self) -> None:
        try:
            while self._running:
                now = clock.mono()
                for scope, deadline in list(self._deadlines.items()):
                    if now >= deadline:
                        await self._expire(scope)
                await asyncio.sleep(min(WATCHDOG_MAX_SLEEP_S, max(WATCHDOG_MIN_SLEEP_S, self._timeout_s/WATCHDOG_DIVISOR)))
        except asyncio.CancelledError:
            pass

    async def snapshot(self) -> dict[str, Any]:
        now = clock.mono()
        return {"version": ATOM_VERSION, "brokers": dict(self._brokers),
                "processed_event_ids": sorted(self._processed_event_ids),
                "financial_truth": self._truth.export(),
                "pending": [{**state, "scope": scope,
                             "remaining_s": max(0.0, self._deadlines.get(scope, now)-now)}
                            for scope, state in self._pending.items()]}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_TRADE_OUTCOME_STATE")
        self._brokers = {str(key): str(value) for key, value in (state.get("brokers") or {}).items()}
        self._processed_event_ids = {str(item) for item in state.get("processed_event_ids", [])}
        self._truth.load(state.get("financial_truth"))
        for item in state.get("pending", []):
            if not isinstance(item, dict) or not item.get("scope"):
                continue
            scope = str(item["scope"]); body = dict(item); body.pop("scope", None)
            remaining = max(0.0, float(body.get("remaining_s", self._timeout_s)))
            self._pending[scope] = body; self._deadlines[scope] = clock.mono()+remaining
            if self._journal is not None and not self._storage_error:
                try: self._journal.save_consumer_state(CONSUMER, scope, body, "snapshot-restore")
                except Exception as exc: self._storage_error = type(exc).__name__

    async def health_check(self) -> HealthStatus:
        details = {"outcomes": self._outcomes, "emitted": self._emitted,
                   "complete": self._completed, "pending": len(self._pending),
                   "duplicates": self._duplicates, "dropped_identity": self._dropped_identity,
                   "incomplete": self._incomplete, "capital_scopes": self._truth.accounts,
                   "identity_incomplete": self._identity_incomplete,
                   "storage_error": self._storage_error}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED", details=details)
        if self._storage_error:
            return HealthStatus(state=HealthState.UNHEALTHY,
                                message="DURABLE_CONSUMER_UNAVAILABLE", details=details)
        if self._pending or self._incomplete or self._dropped_identity:
            return HealthStatus(state=HealthState.DEGRADED,
                                message="OUTCOME_PARTIAL_OR_INCOMPLETE", details=details)
        if not self._outcomes:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_TRADE_OUTCOME | outcomes=0",
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="complete=%d" % self._completed, details=details)
