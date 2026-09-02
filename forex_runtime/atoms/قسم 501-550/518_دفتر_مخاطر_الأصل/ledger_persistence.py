from __future__ import annotations
from typing import Any
from ledger_support import POS_SEP, num, scope, text


def snapshot(atom) -> dict[str, Any]:
    return {"version": atom.__class__.__module__,
            "realized": [{"scope": k, "value": v} for k,v in atom._realized.items()],
            "realized_gross": [{"scope": k, "value": v} for k,v in atom._realized_gross.items()],
            "realized_costs": [{"scope": k, "value": v} for k,v in atom._realized_costs.items()],
            "extracted": [{"scope": k, "value": v} for k,v in atom._extracted.items()],
            "budgets": [{"scope": k, "value": v} for k,v in atom._budgets.items()],
            "positions": list(atom._positions.values()), "specs": atom._specs,
            "last_snapshot": atom._last_snapshot, "seen_trade_ids": list(atom._seen_order),
            "extraction_tickets": atom._extraction_tickets, "brokers": atom._brokers,
            "reservations": [{"account_id": a, "request_id": r, **v}
                             for (a,r),v in atom._reservations.items()]}


def restore(atom, state: dict[str, Any]) -> None:
    # v4.2.0: a non-dict state used to be swallowed with a silent `return`
    # -- self stayed at its empty __init__ defaults (no realized profit, no
    # extracted total, no budgets), no error, no log. self._extracted and
    # self._budgets are what stop the SAME profit from being extracted
    # twice (see 524's milestone ladder, which reads this atom's published
    # ledger) -- silently losing them on a corrupt snapshot risks
    # re-extracting real money already paid out in the prior run.
    if not isinstance(state, dict):
        raise ValueError("INVALID_LEDGER_STATE")
    new_books: dict[str, dict[str, float]] = {}
    new_known: set[str] = set()
    for name in ("realized", "realized_gross", "realized_costs", "extracted", "budgets"):
        book: dict[str, float] = {}
        items = state.get(name, [])
        if not isinstance(items, list): items = []
        for item in items:
            if isinstance(item,dict) and item.get("scope") and num(item.get("value")) is not None:
                book[str(item["scope"])] = num(item["value"]); new_known.add(str(item["scope"]))
        new_books[name] = book
    new_positions: dict[str, Any] = {}
    position_items = state.get("positions", [])
    if not isinstance(position_items, list): position_items = []
    for item in position_items:
        if isinstance(item,dict) and item.get("ticket"):
            key=f"{item.get('source_scope','restored')}{POS_SEP}{item['ticket']}"; new_positions[key]=item
            new_known.add(scope(text(item.get("account_id")),text(item.get("symbol")),text(item.get("broker"))))
    new_specs = dict(state["specs"]) if isinstance(state.get("specs"), dict) else {}
    new_last_snapshot = dict(state["last_snapshot"]) if isinstance(state.get("last_snapshot"), dict) else {}
    seen_items = state.get("seen_trade_ids", [])
    if not isinstance(seen_items, list): seen_items = []
    new_seen_ids = [text(item) for item in seen_items]
    brokers_present = isinstance(state.get("brokers"), dict)
    new_brokers = {str(k):str(v) for k,v in state["brokers"].items()} if brokers_present else {}
    new_reservations: dict[tuple[str,str], dict[str, Any]] = {}
    reservation_items = state.get("reservations", [])
    if not isinstance(reservation_items, list): reservation_items = []
    for item in reservation_items:
        if isinstance(item,dict) and item.get("account_id") and item.get("request_id") and item.get("scope") and num(item.get("amount")) is not None:
            new_reservations[(str(item["account_id"]),str(item["request_id"]))]={"scope":str(item["scope"]),"amount":num(item["amount"])}
    tickets_present = isinstance(state.get("extraction_tickets"), dict)
    new_extraction_tickets = {str(k):str(v) for k,v in state["extraction_tickets"].items()} if tickets_present else {}
    # everything above parsed without raising -- commit as one unit so a
    # future failure point (e.g. a stricter parser added to one field
    # later) cannot leave self holding a torn mix of old and new books.
    atom._realized.update(new_books["realized"])
    atom._realized_gross.update(new_books["realized_gross"])
    atom._realized_costs.update(new_books["realized_costs"])
    atom._extracted.update(new_books["extracted"])
    atom._budgets.update(new_books["budgets"])
    atom._known.update(new_known)
    atom._positions.update(new_positions)
    atom._specs.update(new_specs)
    atom._last_snapshot.update(new_last_snapshot)
    for item in new_seen_ids: atom._remember(item)
    if brokers_present: atom._brokers = new_brokers
    atom._reservations.update(new_reservations)
    if tickets_present: atom._extraction_tickets = new_extraction_tickets
