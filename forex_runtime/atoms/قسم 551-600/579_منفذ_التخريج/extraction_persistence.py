from __future__ import annotations

from typing import Any


def pending_key(account: Any, ticket: Any) -> str:
    return str(account or "").strip() + "|" + str(ticket or "").strip()


def snapshot(atom: Any, version: str) -> dict[str, Any]:
    return {"version": version, "official_time": atom._official_time,
            "pending": [dict(value) for value in atom._pending_by_ticket.values()],
            "pending_full": [{**value, "expected": sorted(value.get("expected", set()))}
                             for value in atom._pending_full.values()],
            "failure_ids": sorted(atom._failure_ids),
            "counts": {"partials": atom._partials, "fulls": atom._fulls,
                       "confirmed": atom._confirmed, "failed": atom._failed,
                       "skipped": atom._skipped}}


def restore(atom: Any, state: dict[str, Any]) -> None:
    if not isinstance(state, dict):
        raise ValueError("INVALID_EXTRACTION_EXECUTOR_STATE")
    pending: dict[str, dict[str, Any]] = {}
    for row in state.get("pending", []):
        if not isinstance(row, dict):
            continue
        account = str(row.get("account_id") or "")
        ticket = str(row.get("ticket") or "").strip()
        if account and ticket:
            pending[pending_key(account, ticket)] = dict(row)
    atom._pending_by_ticket = pending
    atom._pending_full = {}
    for row in state.get("pending_full", []):
        if not isinstance(row, dict) or not row.get("extraction_id"):
            continue
        body = dict(row); body["expected"] = {str(value) for value in row.get("expected", [])}
        atom._pending_full[str(row["extraction_id"])] = body
    atom._failure_ids = {str(value) for value in state.get("failure_ids", [])}
    atom._official_time = float(state.get("official_time") or 0.0)
    counts = state.get("counts") if isinstance(state.get("counts"), dict) else {}
    atom._partials = int(counts.get("partials") or 0)
    atom._fulls = int(counts.get("fulls") or 0)
    atom._confirmed = int(counts.get("confirmed") or 0)
    atom._failed = int(counts.get("failed") or 0)
    atom._skipped = int(counts.get("skipped") or 0)
