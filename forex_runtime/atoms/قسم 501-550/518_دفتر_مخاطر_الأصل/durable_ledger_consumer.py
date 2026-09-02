from __future__ import annotations

from typing import Any

from shared.durable_execution_journal import Journal
from ledger_support import SEP, gross_realized, realized, scope, text

CONSUMER = "518"
# v4.1.0 (2026-08-25): the owner's budget is durable like his realized
# profit. Measured root: budgets lived only in volatile memory (snapshot
# showed budgets: []), so a restart evaporated the owner's 901-written
# decision and the whole protective chain (525 hard stop -> 571 plan ->
# 577 -> 575) slept on NO_BUDGET while a naked leg sat unmanaged.
BUDGET_CONSUMER = "518-budgets"
EVENT_OUT = "risk.asset_ledger.state"


def merge_projection(atom: Any) -> None:
    if atom._journal is None or atom._storage_error:
        return
    try:
        for key, state in atom._journal.consumer_states(CONSUMER).items():
            atom._realized[key] = float(state.get("realized", 0.0))
            atom._realized_gross[key] = float(state.get("gross", 0.0))
            atom._realized_costs[key] = float(state.get("costs", 0.0))
            atom._known.add(key)
        for key, state in atom._journal.consumer_states(BUDGET_CONSUMER).items():
            budget = float(state.get("budget", 0.0))
            if budget > 0:
                atom._budgets[key] = budget
                atom._known.add(key)
    except Exception as exc:
        atom._storage_error = type(exc).__name__


def persist_budget(atom: Any, event_id: str, account: str, key: str,
                   budget: float) -> None:
    """قيد الميزانية دائمًا — idempotent بهوية الحدث، لا يشغّل مخرجات."""
    if atom._journal is None or atom._storage_error or not event_id:
        return

    def reduce(state: dict[str, Any]):
        state["budget"] = float(budget)
        return state, []

    try:
        atom._journal.reduce_consumer_event(
            event_id, account, "BUDGET_SET", event_id, {"budget": float(budget)},
            BUDGET_CONSUMER, key, {"budget": atom._budgets.get(key, 0.0)}, reduce)
    except Exception as exc:
        atom._storage_error = type(exc).__name__


def initialize_consumer(atom: Any, config: dict[str, Any]) -> None:
    atom._journal = Journal(str(config.get("consumer_db_path") or
                                "var/store/asset_ledger_consumer_518.db"))
    try:
        atom._journal.ensure()
    except Exception as exc:
        atom._storage_error = type(exc).__name__
    merge_projection(atom)


async def flush_outbox(atom: Any) -> None:
    if atom._context is None or atom._journal is None or atom._storage_error:
        return
    try:
        for output_id, event_name, payload in atom._journal.pending_outputs():
            await atom._context.publish(event_name, payload)
            atom._journal.mark_emitted(output_id)
            atom._published += 1
    except Exception as exc:
        atom._storage_error = type(exc).__name__


async def consume_trade(atom: Any, payload: dict[str, Any]) -> None:
    if not atom._running or not isinstance(payload, dict):
        return
    atom._trades_seen += 1
    event_id = text(payload.get("event_id"))
    if not event_id:
        atom._missing_event_ids += 1
        return
    owner = atom._owner(payload)
    if owner is None or atom._journal is None or atom._storage_error:
        return
    account, broker = owner
    symbol = text(payload.get("asset_canonical"), text(payload.get("symbol")))
    if not symbol:
        return
    key = scope(account, symbol, broker)
    amount = realized(payload); gross = gross_realized(payload)
    eligible = (str(payload.get("completeness") or "").upper() == "COMPLETE" and
                atom._count_realized and amount is not None and
                account + SEP + text(payload.get("ticket")) not in atom._extraction_tickets)
    initial = {"realized": atom._realized.get(key, 0.0),
               "gross": atom._realized_gross.get(key, 0.0),
               "costs": atom._realized_costs.get(key, 0.0)}

    def reduce(state: dict[str, Any]):
        if not eligible:
            return state, []
        state["realized"] = float(state.get("realized", 0.0)) + float(amount)
        if gross is not None:
            state["gross"] = float(state.get("gross", 0.0)) + gross
            state["costs"] = (float(state.get("costs", 0.0)) +
                              max(0.0, gross-float(amount)))
        realized_book = dict(atom._realized); realized_book[key] = state["realized"]
        gross_book = dict(atom._realized_gross); gross_book[key] = state.get("gross", 0.0)
        costs_book = dict(atom._realized_costs); costs_book[key] = state.get("costs", 0.0)
        body = atom._ledger_payload(realized_book, gross_book, costs_book,
                                    set(atom._known) | {key})
        return state, [("ledger-state:"+event_id, EVENT_OUT, body)]

    try:
        fresh, state = atom._journal.reduce_consumer_event(
            event_id, account, "TRADE_RESULT", event_id, payload,
            CONSUMER, key, initial, reduce)
    except Exception as exc:
        atom._storage_error = type(exc).__name__
        return
    atom._realized[key] = float(state.get("realized", 0.0))
    atom._realized_gross[key] = float(state.get("gross", 0.0))
    atom._realized_costs[key] = float(state.get("costs", 0.0)); atom._known.add(key)
    if not fresh:
        atom._duplicates += 1
        await flush_outbox(atom)
        return
    atom._remember(event_id)
    await flush_outbox(atom)
