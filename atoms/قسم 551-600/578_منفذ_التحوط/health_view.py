"""Health status for 578 -- split out to keep atom.py inside the
constitution's 350 effective-line limit (article 9). Pure read of the
atom's own state, no side effects.
"""
from __future__ import annotations

from typing import Any

from core.contracts.atom import HealthState, HealthStatus

STATUS_EXHAUSTED = "EXHAUSTED"


def build(atom: Any) -> HealthStatus:
    if not atom._running:
        return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
    details = {"seen_targets": atom._seen, "order_requests_emitted": atom._order_requests_emitted,
               "entries_blocked": atom._entries_blocked,
               "snapshot_blocked": atom._snapshot_blocked,
               "position_picture_blocked": atom._position_picture_blocked,
               "position_picture_scopes": len(atom._position_picture),
               "fallback_stops": atom._fallback_stops, "no_stop_skipped": atom._no_stop_skipped,
               "no_identity_skipped": atom._no_identity_skipped,
               "identity_incomplete": atom._identity_incomplete,
               "gate_blocked_unverified": atom._gate_blocked,
               "no_identity_entries": atom._no_identity_entries,
               "pair_store_error": atom._pair_store_error,
               "gate_window": len(atom._gate_window),
               "catastrophe_multiple": atom._catastrophe_multiple, "fallback_stop_frac": atom._fallback_stop_frac,
               "pairs": len(atom._pairs), "retries": atom._retries, "actual": atom._actual,
               "exhausted": atom._exhausted, "flood_suppressed": atom._flood_guard.suppressed,
               "resend_hold_s": atom._flood_guard.hold_s}
    details.update(atom._delta_failures.view(atom._flood_guard, atom._flood_guard.hold_s))
    # v5.3.1: health reports the latest pair per scope, not the counter's
    # history -- a cumulative exhaustion counter used to keep the atom
    # DEGRADED forever after any old exhaustion, even with a healthy new
    # pair working (durable memory keeps the dead ones too). The latest
    # record per (account, symbol) is the present; history stays in the
    # details.
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for p in atom._pairs.values():
        key = (str((p or {}).get("account_id")), str((p or {}).get("symbol")))
        latest[key] = p or {}
    exhausted_now = any(p.get("status") == STATUS_EXHAUSTED for p in latest.values())
    if exhausted_now:
        return HealthStatus(state=HealthState.DEGRADED, message="PAIR_RETRY_EXHAUSTED", details=details)
    if not atom._seen and not atom._pairs:
        return HealthStatus(state=HealthState.HEALTHY,
                            message="READY_AWAITING_FIRST_TARGET_OR_PAIR | requests_emitted=0 pairs=0",
                            details=details)
    return HealthStatus(state=HealthState.HEALTHY, details=details,
                        message="requests_emitted=%d pairs=%d delta_failed=%d" % (
                            atom._order_requests_emitted, len(atom._pairs), atom._delta_failures.total))
