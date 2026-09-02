"""Unhedge state machine -- owner's contract, paper 25 section 8 (2026-08-16).

A pure decision function: same inputs, same verdict, no side effects and no
I/O. Every barrier is therefore breakable from the input alone, which is what
makes it provable.

    PAIR_OPEN
       | explicit unhedge condition
       v
    UNHEDGING
       +-- gate failed ---------> execution_blocked = true (+ reason)
       +-- a leg failed --------> retry that leg only, same pair_id
       +-- retries exhausted ---> EXHAUSTED + freeze
       +-- both legs reconciled -> DIRECTIONAL

THE GOLDEN RULE, and the whole reason this file exists:
    the move to DIRECTIONAL never follows an intent or a request. It follows
    PROVEN execution state. A pair that asked to be unhedged is still hedged.

Two further rules the owner fixed and this module must not quietly bend:
  * `H` is the hedge TARGET derived from `S`. It is never re-derived from the
    realised ratio of the two legs.
  * Unhedging is an operation ON THE EXISTING PAIR. `pair_id` and `cycle_id`
    are carried through unchanged, so it can never become a new pair with a
    new budget.
"""
from __future__ import annotations

from typing import Any

STATUS_OPEN = "COMPLETE"
STATUS_UNHEDGING = "UNHEDGING"
STATUS_DIRECTIONAL = "DIRECTIONAL"
STATUS_EXHAUSTED = "EXHAUSTED"

# The gates, in the order the owner listed them. Order matters only for which
# reason is reported first; any one of them blocks.
GATES: tuple[tuple[str, str], ...] = (
    ("breaker_open", "CIRCUIT_BREAKER_OPEN"),
    ("account_fresh", "ACCOUNT_STALE"),
    ("gate_552_open", "ORDER_GATE_CLOSED"),
    ("gate_575_healthy", "MANAGE_SENDER_UNHEALTHY"),
    ("asset_active", "ASSET_NOT_ACTIVE"),
    ("market_valid", "MARKET_INVALID"),
    ("spread_valid", "SPREAD_INVALID"),
    ("slippage_usable", "SLIPPAGE_UNMEASURABLE"),
    ("margin_after_ok", "INSUFFICIENT_MARGIN_AFTER"),
    ("no_inflight", "INFLIGHT_REQUEST_EXISTS"),
    ("no_lifecycle_conflict", "LIFECYCLE_CONFLICT"),
    ("identity_matches", "IDENTITY_MISMATCH"),
)
# `breaker_open` and `no_inflight` read inverted in the payload, so they are
# normalised below rather than trusted to be phrased consistently by callers.
_INVERTED = {"breaker_open"}


def _flag(state: dict[str, Any], name: str) -> bool:
    """Fail-closed: anything not explicitly True is False.

    A missing key is NOT permission. This is the difference between a gate
    that protects and a gate that is merely present.
    """
    value = state.get(name)
    ok = value is True
    return (not ok) if name in _INVERTED else ok


def blocked_reason(state: dict[str, Any]) -> str | None:
    """First failing gate, or None when every one of them is satisfied."""
    for name, reason in GATES:
        if not _flag(state, name):
            return reason
    return None


def decide(pair: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """One step of the machine. Returns the verdict; changes nothing."""
    status = str(pair.get("status") or "")
    pair_id = pair.get("pair_id")
    keep = {"pair_id": pair_id, "cycle_id": pair.get("cycle_id")}

    def out(**extra: Any) -> dict[str, Any]:
        # `hedge_target` is echoed, never recomputed here: it belongs to S.
        return {**keep, "hedge_target": pair.get("hedge_target"),
                "execution_blocked": False, "block_reason": None,
                "retry_leg": None, "freeze": False, **extra}

    if status == STATUS_DIRECTIONAL:
        return out(status=STATUS_DIRECTIONAL, action="NONE")

    # ── entry: only an explicit, satisfied condition opens the door ──────────
    if status == STATUS_OPEN:
        if state.get("unhedge_signal") is not True:
            return out(status=STATUS_OPEN, action="NONE")
        reason = blocked_reason(state)
        if reason is not None:
            # The target is recorded; the execution is not started. A blocked
            # unhedge must never close the other leg as a consolation.
            return out(status=STATUS_OPEN, action="NONE",
                       execution_blocked=True, block_reason=reason)
        return out(status=STATUS_UNHEDGING, action="BEGIN_UNHEDGE")

    if status != STATUS_UNHEDGING:
        return out(status=status or STATUS_OPEN, action="NONE")

    # ── in flight ───────────────────────────────────────────────────────────
    legs = pair.get("legs") if isinstance(pair.get("legs"), dict) else {}
    failed = [role for role, leg in sorted(legs.items())
              if str((leg or {}).get("status") or "") == "FAILED"]

    if failed:
        role = failed[0]
        attempts = int((legs[role] or {}).get("attempts") or 0)
        limit = int(pair.get("max_attempts") or 0)
        if attempts >= limit:
            # Exhausted is terminal and loud. Never CLOSE_ALL, never close the
            # surviving leg to "tidy up".
            return out(status=STATUS_EXHAUSTED, action="ESCALATE", freeze=True,
                       block_reason="RETRIES_EXHAUSTED")
        reason = blocked_reason(state)
        if reason is not None:
            return out(status=STATUS_UNHEDGING, action="NONE",
                       execution_blocked=True, block_reason=reason)
        return out(status=STATUS_UNHEDGING, action="RETRY_LEG", retry_leg=role)

    # ── the only path to DIRECTIONAL ────────────────────────────────────────
    # Proven state, not intent: every leg reconciled against the broker AND the
    # net exposure actually reached the target. Either one alone is a lie.
    reconciled = bool(legs) and all(
        str((leg or {}).get("status") or "") == "RECONCILED" for leg in legs.values())
    net_ok = state.get("net_matches_target") is True
    if reconciled and net_ok:
        return out(status=STATUS_DIRECTIONAL, action="COMPLETE_UNHEDGE")

    return out(status=STATUS_UNHEDGING, action="AWAIT_EXECUTION",
               block_reason=None if reconciled else "LEGS_NOT_RECONCILED")
