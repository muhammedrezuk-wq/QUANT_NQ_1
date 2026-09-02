from __future__ import annotations

from typing import Any

import clock
from core.contracts.atom import HealthState, HealthStatus
from shared.financial_scope import text


def snapshot(atom: Any, version: str) -> dict[str, Any]:
    return {
        "version": version,
        # (٢٠٢٦-٠٨-٢٥) بوّابة `enabled` تُحفظ وتُستعاد — كانت تضيع مع كل
        # إقلاع فتعود مفتوحة (`fail-open` على آخر بوّابة قبل الوسيط، مقيس).
        "enabled": bool(getattr(atom, "_enabled", True)),
        "global_halted": atom._global_halted,
        "halted_accounts": dict(atom._halted_accounts),
        "snapshots": [
            {"snapshot_id": key, **value} for key, value in atom._snapshots.items()
        ],
        "margin_verdicts": [
            {"account_id": account, "request_id": request, **value}
            for (account, request), value in atom._margin_verdicts.items()
        ],
    }


async def restore(atom: Any, state: dict[str, Any], fail_closed: str) -> None:
    if not isinstance(state, dict):
        atom._global_halted = True
        atom._restore_error = fail_closed
        raise ValueError(fail_closed)
    global_halted = state.get("global_halted", state.get("halted"))
    accounts = state.get("halted_accounts", {})
    if not isinstance(global_halted, bool) or not isinstance(accounts, dict):
        atom._global_halted = True
        atom._restore_error = fail_closed
        raise ValueError(fail_closed)
    atom._global_halted = global_halted
    atom._halted_accounts = {
        str(key): str(value) for key, value in accounts.items() if str(key)
    }
    # بوّابة مقفولة قبل الإيقاف تبقى مقفولة بعده — لا fail-open صامت.
    stored_enabled = state.get("enabled")
    if isinstance(stored_enabled, bool):
        atom._enabled = stored_enabled
    atom._snapshots = {}
    for row in state.get("snapshots") or []:
        if not isinstance(row, dict):
            continue
        snapshot_id = text(row.get("snapshot_id"))
        if not snapshot_id:
            continue
        atom._snapshots[snapshot_id] = {
            "decision_id": row.get("decision_id"),
            "gate_request_id": row.get("gate_request_id"),
            "snapshot_status": row.get("snapshot_status"),
            "usable_for_new_exposure": row.get("usable_for_new_exposure") is True,
            "usable_for_protection": row.get("usable_for_protection") is True,
            "produced_at": row.get("produced_at"),
            "measured_at": row.get("measured_at"),
        }
    atom._margin_verdicts = {}
    for row in state.get("margin_verdicts") or []:
        if not isinstance(row, dict):
            continue
        account = text(row.get("account_id"))
        request_id = text(row.get("request_id"))
        if not account or not request_id:
            continue
        atom._margin_verdicts[(account, request_id)] = {
            "approved": row.get("approved") is True,
            "reason": text(row.get("reason")),
            "required_margin": row.get("required_margin"),
            "free_margin": row.get("free_margin"),
            "measured_at": row.get("measured_at"),
        }
    atom._restore_error = ""
    await atom._publish_gate()


def health(atom: Any) -> HealthStatus:
    if not atom._running:
        return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
    details = {
        "enabled": atom._enabled,
        # (٢٠٢٦-٠٨-٢٥) عمى القائمة كان يقتل بصمت — صارت حالتها معلنة.
        "whitelist_seen": bool(getattr(atom, "_whitelist_seen", False)),
        "allowed_global": len(getattr(atom, "_allowed", ()) or ()),
        "allowed_accounts": {a: sorted(v) for a, v in
                             (getattr(atom, "_allowed_by_account", {}) or {}).items()},
        "global_halted": atom._global_halted,
        "halted_accounts": dict(atom._halted_accounts),
        "restore_error": atom._restore_error,
        "seen": atom._seen,
        "decision_finalized": atom._decisions_finalized,
        "rejected": atom._rejected,
        "clock_quality": clock.quality(),
        "spread_scopes": len(atom._spread),
        "reconcile_scopes": len(atom._reconcile),
        "reference_symbols": len(atom._reference),
        "reconcile_blocked": atom._reconcile_blocked,
        "reference_blocked": atom._reference_blocked,
        "identity_blocked": atom._identity_blocked,
        "exposure_scopes": len(atom._exposure),
        "exposure_blocked": atom._exposure_blocked,
        "parent_decision_blocked": atom._parent_decision_blocked,
        "margin_verdict_blocked": atom._margin_verdict_blocked,
        "snapshot_validity_blocked": atom._snapshot_validity_blocked,
        "margin_verdicts_tracked": len(atom._margin_verdicts),
        "snapshots_tracked": len(atom._snapshots),
        "identity_recovered": atom._identity_recovered,
        "identity_incomplete": atom._identity_incomplete,
    }
    if atom._seen == 0:
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="READY_AWAITING_FIRST_LEGAL_ORDER | finalized=0 rejected=0",
            details=details,
        )
    return HealthStatus(
        state=HealthState.HEALTHY,
        message="finalized=%d rejected=%d" % (
            atom._decisions_finalized, atom._rejected
        ),
        details=details,
    )
