"""Crash recovery contract -- owner's ruling 2026-08-16.

A clean shutdown is a courtesy, not a guarantee. Power does not ask. So the
system must not depend on being asked to stop.

    snapshot  -> the last CONFIRMED state, nothing newer
    runtime    -> UNKNOWN_AFTER_CRASH, never zero and never invented
    truth      -> found by reconciling with the execution source

THE CASE THAT MATTERS MOST, in the owner's words:
    if power is cut after an order is sent and before its confirmation
    arrives, the system may NOT assume on boot that it failed, and may NOT
    assume it succeeded. It is UNCONFIRMED, and the truth is sought from the
    execution source -- which is exactly why `commands` is not `trade_store`.

A pure decision layer. No I/O, no clock: every barrier breaks from the input.
"""
from __future__ import annotations

from typing import Any

CLEAN = "CLEAN_SHUTDOWN"
CRASH = "CRASH"
UNKNOWN_AFTER_CRASH = "UNKNOWN_AFTER_CRASH"

# What a boot may conclude about work that was in flight when the lights died.
RESOLVED_FILLED = "RESOLVED_FILLED"
RESOLVED_NOT_SENT = "RESOLVED_NOT_SENT"
STILL_UNCONFIRMED = "STILL_UNCONFIRMED"


def classify_boot(marker: Any) -> dict[str, Any]:
    """Did the last life end on purpose, or was it cut off?

    The marker is written LAST in a clean shutdown. Its absence is the only
    honest evidence of a crash -- and absence is never read as "fine".
    """
    if not isinstance(marker, dict):
        return {"kind": CRASH, "trusted": False, "reason": "NO_SHUTDOWN_MARKER"}
    if marker.get("clean") is not True:
        return {"kind": CRASH, "trusted": False, "reason": "MARKER_NOT_CLEAN"}
    if marker.get("snapshot_verified") is not True:
        # It stopped on purpose but never proved its snapshot. Treat the state
        # as a crash would: unverified is not confirmed.
        return {"kind": CRASH, "trusted": False,
                "reason": "SHUTDOWN_WITHOUT_VERIFIED_SNAPSHOT"}
    return {"kind": CLEAN, "trusted": True, "reason": "CLEAN_AND_VERIFIED"}


def runtime_gap(snapshot: Any, boot: dict[str, Any]) -> dict[str, Any]:
    """What we know, and what we must refuse to claim, about the gap.

    Everything that happened between the last snapshot and the cut is gone
    from memory. It is NOT zero. Reporting a counter of zero after a crash is
    the same lie as inventing a higher one.
    """
    body = (snapshot or {}).get("payload") if isinstance(snapshot, dict) else None
    if boot["kind"] == CLEAN:
        return {"state": body, "counters": "RESTORED", "gap": "NONE",
                "trade_allowed": True, "reason": boot["reason"]}
    if body is None:
        return {"state": None, "counters": UNKNOWN_AFTER_CRASH, "gap": "TOTAL",
                "trade_allowed": False, "reason": "CRASH_WITHOUT_SNAPSHOT"}
    return {"state": body, "counters": UNKNOWN_AFTER_CRASH,
            "gap": "SINCE_LAST_SNAPSHOT", "trade_allowed": False,
            "reason": "CRASH_STATE_IS_A_FLOOR_NOT_A_TRUTH"}


def resolve_inflight(command: Any, execution_source: Any) -> dict[str, Any]:
    """The order that was in the air when the lights went out.

    `execution_source` is what the BROKER says -- trade events, positions,
    deal history. Never `commands`, which only records what we asked for.
    """
    if command is None:
        return {"status": RESOLVED_NOT_SENT, "trade_allowed": True,
                "reason": "NOTHING_IN_FLIGHT"}
    if execution_source is None:
        # We cannot see the broker yet. Silence is not "it failed".
        return {"status": STILL_UNCONFIRMED, "trade_allowed": False,
                "reason": "NO_EXECUTION_SOURCE"}

    key = str((command or {}).get("request_id") or "")
    hits = [row for row in execution_source
            if isinstance(row, dict) and str(row.get("request_id") or "") == key]
    if hits:
        return {"status": RESOLVED_FILLED, "trade_allowed": True,
                "reason": "FOUND_IN_EXECUTION_SOURCE", "evidence": hits[0]}
    if (command or {}).get("reached_broker") is True:
        # It left our side. The broker has not shown it. We do NOT decide.
        return {"status": STILL_UNCONFIRMED, "trade_allowed": False,
                "reason": "SENT_BUT_NOT_FOUND"}
    return {"status": RESOLVED_NOT_SENT, "trade_allowed": True,
            "reason": "NEVER_LEFT_OUR_SIDE"}


def may_resume(boot: dict[str, Any], gap: dict[str, Any],
               inflight: dict[str, Any], reconciled: Any) -> dict[str, Any]:
    """The single gate a booting system passes before it may trade again."""
    blockers = []
    if boot["kind"] != CLEAN:
        blockers.append(boot["reason"])
    if gap.get("trade_allowed") is not True:
        blockers.append(gap["reason"])
    if inflight.get("trade_allowed") is not True:
        blockers.append(inflight["reason"])
    if reconciled is not True:
        blockers.append("NOT_RECONCILED_WITH_BROKER")
    return {"resume": not blockers, "blockers": blockers,
            "reason": "READY" if not blockers else blockers[0]}
