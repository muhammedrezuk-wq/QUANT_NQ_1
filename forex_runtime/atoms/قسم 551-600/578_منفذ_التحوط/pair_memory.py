"""Pair memory across restarts -- snapshot contract, owner's ruling 2026-08-16.

Kept beside the atom only because the atom hit its size limit; the rule is
still one atom, one job.

THE LINE THE OWNER DREW, and the reason the reconcile step exists:
    the snapshot is the source of CONTINUITY, never the source of TRUTH.
    If memory says a leg is open and the bridge shows no such ticket, we do
    not pick a winner from memory. We raise the conflict and freeze the path.
"""
from __future__ import annotations

import copy
from typing import Any

from shared.snapshot_state import CONFIRMED, VALID, digest_of, grade, reconcile


def seal(version: str, counter: int, pairs: dict[str, Any],
         official_time: Any, epoch: Any, flood_guard: Any = None) -> dict[str, Any]:
    """The record this atom persists, sealed so tampering is detectable.

    Deep-copies every pair (legs included): the caller may hand this to a
    background thread for serialization (durable persist) or hold onto it
    past the current turn (snapshot/restore) -- either way it must not
    still be aliased to the live, mutable `self._pairs`, or a handler that
    runs while the copy is in flight could tear the write or silently
    change what gets persisted.
    """
    body = {"version": version, "counter": counter,
            "pairs": copy.deepcopy(pairs or {}),
            "flood_guard": flood_guard if isinstance(flood_guard, dict) else {}}
    return {"schema_version": 1,
            "written_at": float(official_time or 0.0),
            "session_epoch": epoch, "payload": body,
            "digest": digest_of(body)}


def unseal(state: Any) -> dict[str, Any]:
    """Refused WHOLE unless VALID -- a half-remembered pair is not a pair."""
    verdict = grade(state)
    if verdict["grade"] != VALID:
        return {"grade": verdict["grade"], "pairs": {}, "counter": None,
                "flood_guard": {}, "reason": verdict["reason"]}
    body = state["payload"]
    pairs = {k: dict(v) for k, v in (body.get("pairs") or {}).items()
             if isinstance(k, str) and isinstance(v, dict)}
    try:
        counter = int(float(body.get("counter")))
    except (TypeError, ValueError):
        counter = None
    return {"grade": VALID, "pairs": pairs, "counter": counter,
            "flood_guard": body.get("flood_guard") if isinstance(body.get("flood_guard"), dict) else {},
            "reason": verdict["reason"]}


def check_against_broker(pairs: dict[str, Any], rows: Any) -> dict[str, Any]:
    """Validate remembered legs against the live broker picture."""
    if not isinstance(rows, list):
        return {"reconciled": False, "reason": "NO_LIVE_PICTURE", "conflict": {}}
    live = {str(p.get("ticket")): p for p in rows
            if isinstance(p, dict) and p.get("ticket") is not None}
    remembered: dict[str, Any] = {}
    for pair in (pairs or {}).values():
        for leg in ((pair or {}).get("legs") or {}).values():
            ticket = (leg or {}).get("ticket")
            if ticket is not None:
                remembered[str(ticket)] = leg
    verdict = reconcile(remembered, live)
    ok = verdict["verdict"] == CONFIRMED
    return {"reconciled": ok, "reason": verdict["reason"],
            "conflict": {} if ok else {
                "only_in_snapshot": verdict["only_in_snapshot"],
                "only_in_live": verdict["only_in_live"]}}
