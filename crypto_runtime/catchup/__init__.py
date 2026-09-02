"""Archive catch-up decision -- owner's contract 2026-08-16.

A pure function. No I/O, no clock of its own, no side effects: the caller
brings the persisted state and the official time, and gets back a verdict.
That is what makes every barrier breakable from the input alone.

WHY THIS EXISTS, measured on the live core:
    806 publishes SYS_DAY on the UTC midnight boundary, and restarts the
    countdown on every process start. Counters read
    SYS_HOUR=0 SYS_DAY=0 after 44 minutes of uptime. A system that is
    restarted often therefore never crosses the boundary, and a daily task
    waits for a deadline that never arrives. Not broken -- postponed to a
    time that may never come.

    The fix is NOT to change SYS_DAY. It is to remember the last SUCCESSFUL
    archive and notice, at startup, that the window was missed.

THE RULE THAT MUST NOT BEND:
    `last_success` is written ONLY after the copy completed, the archive was
    verified, and the source was confirmed intact. Never at the start. A run
    that dies half-way leaves the old state untouched, so the next startup
    retries it -- instead of believing a job it never finished.
"""
from __future__ import annotations

from typing import Any

NEVER_RAN = "NEVER_RAN"
CATCHUP_REQUIRED = "CATCHUP_REQUIRED"
ARCHIVING = "ARCHIVING"
ARCHIVED = "ARCHIVED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"

DEFAULT_WINDOW_S = 86_400.0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def decide(state: dict[str, Any], now: Any,
           window_s: float = DEFAULT_WINDOW_S) -> dict[str, Any]:
    """What should archiving do at this moment? Decides; performs nothing."""
    official = _number(now)
    if official is None:
        # No official clock reached us. We do not guess with a wall clock, and
        # we do not start work we cannot timestamp.
        return {"status": SKIPPED, "run": False, "reason": "NO_OFFICIAL_TIME",
                "age_s": None}

    last = _number((state or {}).get("last_success"))
    if last is None:
        return {"status": NEVER_RAN, "run": True, "reason": "NO_SUCCESSFUL_RUN",
                "age_s": None}

    age = official - last
    if age < 0:
        # A stamp from the future means the persisted state cannot be trusted.
        # Fail-closed: run, rather than sleep on a number we cannot explain.
        return {"status": CATCHUP_REQUIRED, "run": True,
                "reason": "LAST_SUCCESS_IN_FUTURE", "age_s": age}
    if age >= window_s:
        return {"status": CATCHUP_REQUIRED, "run": True,
                "reason": "WINDOW_MISSED", "age_s": age}
    return {"status": SKIPPED, "run": False, "reason": "WITHIN_WINDOW",
            "age_s": age}


def outcome(copied: Any, verified: Any, source_intact: Any,
            started_at: Any, finished_at: Any) -> dict[str, Any]:
    """The verdict of one run, and whether `last_success` may be written.

    ARCHIVED is not "a file was written". It is: copied AND verified AND the
    source still there. Anything less is FAILED, and FAILED never advances
    `last_success` -- the source is kept and the next startup tries again.
    """
    ok = copied is True and verified is True and source_intact is True
    stamp = _number(finished_at)
    if ok and stamp is None:
        # A run we cannot timestamp cannot be recorded as the last success,
        # or the next startup would compare against nothing.
        return {"status": FAILED, "persist_last_success": False,
                "last_success": None, "reason": "NO_FINISH_STAMP",
                "source_kept": True}
    if not ok:
        reason = ("NOT_COPIED" if copied is not True else
                  "NOT_VERIFIED" if verified is not True else "SOURCE_LOST")
        return {"status": FAILED, "persist_last_success": False,
                "last_success": None, "reason": reason,
                # The source is never removed on a failed run. This is the
                # whole point of verifying before deleting.
                "source_kept": True}
    return {"status": ARCHIVED, "persist_last_success": True,
            "last_success": stamp, "reason": "VERIFIED",
            "source_kept": True,
            "duration_s": (stamp - (_number(started_at) or stamp))}
