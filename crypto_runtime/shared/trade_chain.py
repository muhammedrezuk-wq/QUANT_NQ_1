"""Trade chain contract -- owner's ruling 2026-08-16.

    decision -> request_id -> command -> confirmation -> trade_event
             -> trade store -> reader -> result / cost / slippage

A pure decision layer: no I/O, no clock. Every barrier breaks from the input.

THE THREE LINES THE OWNER DREW:

  1. `commands` is WHAT WE ASKED FOR. `trade_store` is WHAT WAS CONFIRMED TO
     HAVE HAPPENED. The gap between them is exactly where slippage, partial
     fills and execution cost live -- so they must never be the same record,
     and the reader must never rebuild a fill from `commands`.

  2. A command with no confirmation stays UNCONFIRMED. Never FILLED by
     assumption. "We sent it" is not "it happened".

  3. After A0, `request_id` alone is not an identity. A trade is matched on
     (session stamp, request_id, pair_id) so a restart cannot make an old
     trade look like the result of a new one.
"""
from __future__ import annotations

from typing import Any

UNCONFIRMED = "UNCONFIRMED"
FILLED = "FILLED"
PARTIAL = "PARTIAL"
ORPHAN_CONFIRMATION = "ORPHAN_CONFIRMATION"
DUPLICATE = "DUPLICATE"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"

# The chain links, in order. A break in any one of them is DEGRADED, never
# silently skipped -- a trade we cannot prove is not a trade we may count.
LINKS = ("command", "confirmation", "trade_event", "stored", "read")


def identity(record: Any) -> tuple:
    """(session stamp, request_id, pair_id) -- never request_id alone."""
    row = record or {}
    return (str(row.get("session_epoch") or ""),
            str(row.get("request_id") or ""),
            str(row.get("pair_id") or ""))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def match(command: Any, confirmation: Any) -> dict[str, Any]:
    """Can this confirmation be attributed to this command, and what state?"""
    if command is None:
        return {"status": ORPHAN_CONFIRMATION, "matched": False,
                "reason": "NO_SUCH_REQUEST", "usable": False}
    if confirmation is None:
        # We asked; nobody said it happened. That is UNCONFIRMED, forever, or
        # until a confirmation arrives. It is never promoted by waiting.
        return {"status": UNCONFIRMED, "matched": False,
                "reason": "NO_CONFIRMATION", "usable": False}
    if identity(command) != identity(confirmation):
        return {"status": IDENTITY_MISMATCH, "matched": False,
                "reason": "IDENTITY_DIFFERS", "usable": False,
                "expected": identity(command), "got": identity(confirmation)}

    asked = _number(command.get("volume"))
    filled = _number(confirmation.get("volume"))
    if asked is not None and filled is not None and filled < asked:
        return {"status": PARTIAL, "matched": True, "reason": "PARTIAL_FILL",
                "usable": True, "requested_volume": asked, "filled_volume": filled}
    return {"status": FILLED, "matched": True, "reason": "CONFIRMED",
            "usable": True}


def is_duplicate(record: Any, seen: Any) -> bool:
    """Same identity twice is the same trade, not a second one."""
    return identity(record) in set(seen or ())


def slippage(command: Any, confirmation: Any) -> dict[str, Any]:
    """Signed so positive always means WORSE, on either side."""
    verdict = match(command, confirmation)
    if not verdict["usable"]:
        return {"measured": False, "usable": False, "reason": verdict["reason"]}
    asked = _number((command or {}).get("requested_price"))
    got = _number((confirmation or {}).get("executed_price"))
    if asked is None or got is None:
        return {"measured": False, "usable": False, "reason": "NO_PRICES"}
    raw = got - asked
    side = str((command or {}).get("side") or "").upper()
    adverse = raw if side == "BUY" else -raw
    return {"measured": True, "usable": True, "reason": "MEASURED",
            "slippage_price": adverse, "adverse": adverse > 0.0}


def chain_health(links: Any) -> dict[str, Any]:
    """A broken link degrades the chain and names itself. No silent gaps."""
    state = links or {}
    broken = [name for name in LINKS if state.get(name) is not True]
    if not broken:
        return {"healthy": True, "degraded": False, "broken": [],
                "reason": "CHAIN_COMPLETE", "provable": True}
    return {"healthy": False, "degraded": True, "broken": broken,
            "reason": "CHAIN_BROKEN_AT_" + broken[0].upper(),
            # A trade whose chain is broken cannot be proven, so it must not be
            # counted as a result -- neither as a win nor as a loss.
            "provable": False}
