"""Persistent snapshot contract -- owner's ruling 2026-08-16.

A pure decision layer. No filesystem, no clock, no I/O: the caller brings what
it read and what it computed, and gets back a verdict it may act on. Every
barrier is therefore breakable from the input alone.

WHY THIS IS SEPARATE FROM THE SESSION STAMP -- the owner drew this line and it
must not blur:

    session stamp      -> "is this identifier unique across lives?"
    persistent snapshot -> "do I know where I was before the restart?"

The stamp inside `pair_id` fixed a collision. It does NOT remember state. A
system with unique ids and no snapshot still wakes up amnesiac; a system with
snapshots and colliding ids remembers the wrong thing. Both are needed.

THE RULE, the same one the archive contract established:
    write -> verify -> commit.
    A snapshot that was written but not verified is NOT the official state.
    And a failed new snapshot never destroys the last good one.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

VALID = "VALID"
PARTIAL = "PARTIAL"
CORRUPT = "CORRUPT"
UNKNOWN = "UNKNOWN"

# What a snapshot must carry to be considered whole. A missing field makes it
# PARTIAL -- never silently `{}`.
REQUIRED = ("schema_version", "written_at", "session_epoch", "payload", "digest")

# What each non-VALID grade is allowed to do. No guessing anywhere: the policy
# is declared here, once, instead of being improvised at each call site.
POLICY = {
    VALID: {"restore": True, "trade": True, "reason": "VERIFIED"},
    PARTIAL: {"restore": False, "trade": False, "reason": "INCOMPLETE_SNAPSHOT"},
    CORRUPT: {"restore": False, "trade": False, "reason": "DIGEST_MISMATCH"},
    UNKNOWN: {"restore": False, "trade": False, "reason": "NO_SNAPSHOT"},
}


def digest_of(payload: Any) -> str:
    """Stable digest of the payload. Same content, same string, any run."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def grade(snapshot: Any) -> dict[str, Any]:
    """VALID / PARTIAL / CORRUPT / UNKNOWN -- and what it permits."""
    if snapshot is None:
        return {"grade": UNKNOWN, "missing": list(REQUIRED), **POLICY[UNKNOWN]}
    if not isinstance(snapshot, dict):
        return {"grade": CORRUPT, "missing": [], **POLICY[CORRUPT],
                "reason": "NOT_A_RECORD"}

    missing = [key for key in REQUIRED if snapshot.get(key) is None]
    if missing:
        # A record short of its own contract is PARTIAL, not empty state.
        # Booting with `state = {}` because a field was absent is exactly the
        # amnesia this contract exists to prevent.
        return {"grade": PARTIAL, "missing": missing, **POLICY[PARTIAL]}

    if digest_of(snapshot["payload"]) != snapshot["digest"]:
        return {"grade": CORRUPT, "missing": [], **POLICY[CORRUPT]}

    return {"grade": VALID, "missing": [], **POLICY[VALID]}


def commit(candidate: Any, previous: Any) -> dict[str, Any]:
    """Should this new snapshot become the official state?

    Only a VALID candidate is committed. Anything else leaves `previous`
    exactly as it was -- a failed save must never cost us the last good one.
    """
    verdict = grade(candidate)
    if verdict["grade"] == VALID:
        return {"commit": True, "official": candidate, "grade": VALID,
                "reason": "VERIFIED", "previous_kept": True}
    return {"commit": False, "official": previous, "grade": verdict["grade"],
            "reason": verdict["reason"], "previous_kept": previous is not None}


CONFIRMED = "CONFIRMED"
CONFLICT = "CONFLICT"
NO_EVIDENCE = "NO_EVIDENCE"


def reconcile(restored: Any, live: Any) -> dict[str, Any]:
    """Owner's ruling 2026-08-16: the snapshot is the source of CONTINUITY,
    never the source of TRUTH.

        snapshot -> restore -> VALIDATE -> live state
    not
        snapshot -> believe everything

    Concretely: if the snapshot says a leg is open and the bridge shows no
    such position, we do NOT pick a winner from memory. We raise a conflict
    and freeze the affected path. Choosing silently is how a restarted system
    trades against a position it only imagines.

    `restored` and `live` are maps of ticket -> record. `live` being None means
    the broker picture has not arrived yet -- which is NOT the same as "no
    positions", and is never read as agreement.
    """
    if live is None:
        return {"verdict": NO_EVIDENCE, "reason": "NO_LIVE_PICTURE",
                "freeze_path": True, "trade_allowed": False,
                "only_in_snapshot": [], "only_in_live": []}

    remembered = set(restored or {})
    actual = set(live or {})
    ghosts = sorted(remembered - actual)     # نتذكّرها ولا وجود لها
    strangers = sorted(actual - remembered)  # موجودة ولا نعرفها

    if ghosts or strangers:
        return {"verdict": CONFLICT,
                "reason": "SNAPSHOT_DISAGREES_WITH_BROKER",
                "freeze_path": True, "trade_allowed": False,
                "only_in_snapshot": ghosts, "only_in_live": strangers}

    return {"verdict": CONFIRMED, "reason": "SNAPSHOT_MATCHES_BROKER",
            "freeze_path": False, "trade_allowed": True,
            "only_in_snapshot": [], "only_in_live": []}


def resume(snapshot: Any, live_epoch: Any) -> dict[str, Any]:
    """What a booting atom may assume, and what it must refuse to assume."""
    verdict = grade(snapshot)
    if verdict["grade"] != VALID:
        return {"state": None, "restored": False, "grade": verdict["grade"],
                "reason": verdict["reason"], "trade_allowed": False,
                # Counters restart at zero ONLY when nothing valid was found,
                # and the caller is told so explicitly rather than handed a
                # zero that looks like a real reading.
                "counters_reset": True}
    payload = snapshot["payload"]
    return {"state": payload, "restored": True, "grade": VALID,
            "reason": "VERIFIED", "trade_allowed": True,
            "counters_reset": False,
            # The stamp of the life we are resuming INTO, kept beside the one
            # we are resuming FROM. Identity uniqueness and state continuity
            # are two different jobs and both must be visible.
            "previous_epoch": snapshot.get("session_epoch"),
            "live_epoch": live_epoch}
