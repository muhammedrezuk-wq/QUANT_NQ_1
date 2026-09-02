"""Neutral-pair status resolution for 578.

Split out of atom.py to keep the atom inside the constitution's 300 effective
line limit (article 9) while the last-resort stop contract was added. Pure
function, no state: the pair's status is a function of its legs alone.
"""
from __future__ import annotations

STATUS_REQUESTED = "REQUESTED"
STATUS_ACTUAL = "ACTUAL"
STATUS_EXHAUSTED = "EXHAUSTED"
STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL = "PARTIAL"
# Unhedge contract (paper 25 section 8, owner's ruling 2026-08-16): the pair
# moves from hedged to net directional exposure when `H_target` reaches zero.
# This is a DECLARED state, not an implicit one, and it is NOT a close: only
# the required exposure `G` is kept, and the winning leg is never the one
# chosen to survive -- the surviving direction is the direction of `Q_net`.
STATUS_UNHEDGING = "UNHEDGING"
STATUS_DIRECTIONAL = "DIRECTIONAL"

# The owner's contract names beside the names this code has used since it was
# built. Published alongside the status so a human reads the contract's
# language, not the implementation's.
CONTRACT_NAMES = {
    STATUS_REQUESTED: "PAIR_REQUESTED",
    STATUS_PARTIAL: "PARTIAL_PAIR",
    STATUS_COMPLETE: "PAIR_OPEN",
    STATUS_EXHAUSTED: "PAIR_ABORTED",
    STATUS_UNHEDGING: "UNHEDGING",
    STATUS_DIRECTIONAL: "DIRECTIONAL_EXPOSURE",
}


def contract_name(status: str) -> str:
    """The status in the contract's language. Changes nothing, reads clearly."""
    return CONTRACT_NAMES.get(status, status or STATUS_REQUESTED)


def _num(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def new_pair(pair_id: str, payload: dict, max_attempts: int) -> dict:
    """سجلّ الزوج كما كان يُبنى داخل الذرّة حرفًا بحرف — نُقل هنا لا أكثر.

    البند ١٢: الذرّة تجاوزت حدّ الـ٣٠٠ سطر بعد إضافات البند ٦٣، وبناء الزوج
    بيانات محضة تخصّ هذا الملفّ أصلًا.
    """
    return {"pair_id": pair_id,
            "account_id": str(payload.get("account_id") or ""),
            "symbol": str(payload.get("symbol") or ""),
            "volume": _num(payload.get("pair_volume", payload.get("volume"))),
            "legs": {}, "status": STATUS_REQUESTED, "max_attempts": max_attempts}


def leg_entry(payload: dict, pair: dict, role: str, request_id: str) -> dict:
    """قيد الساق كما كان — منقول بلا تغيير في حقل واحد."""
    return {"request_id": request_id,
            "account_id": str(payload.get("account_id") or pair["account_id"]),
            "symbol": str(payload.get("symbol") or pair["symbol"]),
            "side": str(payload.get("side") or role).upper(),
            "volume": _num(payload.get("volume")),
            "attempt": int(_num(payload.get("attempt")) or 1),
            "status": STATUS_REQUESTED,
            "request": dict(payload)}


def pair_status(legs: dict, current: str) -> str:
    """COMPLETE only when BOTH legs are actual; a dead leg makes the pair dead."""
    statuses = [item.get("status") for item in legs.values()]
    if len(statuses) == 2 and all(x == STATUS_ACTUAL for x in statuses):
        return STATUS_COMPLETE
    if any(x == STATUS_EXHAUSTED for x in statuses):
        return STATUS_EXHAUSTED
    if any(x == STATUS_ACTUAL for x in statuses):
        return STATUS_PARTIAL
    return current or STATUS_REQUESTED
