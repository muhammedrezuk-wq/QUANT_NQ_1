# -*- coding: utf-8 -*-
"""Change Governor (850) — intelligence paper Phase 6.

The ONLY writer of record for adaptive change decisions — and even it never
touches production parameters: its maximum authority is
``APPROVED_FOR_SHADOW`` (§21). Full activation stays a separate owner-gated
step.

Gates (§21 checklist): evidence sufficiency · sample size · change bounds ·
regime compatibility · reversibility · adaptation limits (§22) · churn
protection (§23) · kill-switch awareness (§27).
"""

from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

DEFAULT_MIN_EVIDENCE_WINDOWS = 10
DEFAULT_MAX_CHANGE_PER_STEP = 0.25
DEFAULT_MAX_CHANGE_PER_DAY = 0.50
DEFAULT_MAX_ACTIVE = 2
DEFAULT_MIN_DWELL_S = 300.0
DEFAULT_COOLDOWN_S = 120.0
DEFAULT_MAX_PER_WINDOW = 5
BOUNDS_HEADROOM = 4.0

EVENT_PROPOSE = "recalibration.proposed"
EVENT_KILL = "adaptation.kill_switch.state"
EVENT_OUT = "experiment.state"
EVENT_LIMITS = "adaptation.limits.state"

STATUS_REJECTED = "REJECTED"
STATUS_SHADOW = "APPROVED_FOR_SHADOW"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_QUIET = "NO_CANDIDATES_YET"


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        # §22 adaptation limits — defaults are deliberately conservative.
        self._min_evidence_windows = DEFAULT_MIN_EVIDENCE_WINDOWS
        self._max_change_per_step = DEFAULT_MAX_CHANGE_PER_STEP
        self._max_change_per_day = DEFAULT_MAX_CHANGE_PER_DAY
        self._max_active = DEFAULT_MAX_ACTIVE
        self._min_dwell_s = DEFAULT_MIN_DWELL_S
        self._cooldown_s = DEFAULT_COOLDOWN_S
        self._max_per_window = DEFAULT_MAX_PER_WINDOW
        self._experiments: dict[str, dict[str, Any]] = {}
        self._active: list[str] = []
        self._changes_today = 0.0
        self._window_changes: list[float] = []
        self._last_change_at = 0.0
        self._kill_active = False
        self._kill_reason = ""
        # Build 3: drops counted with reasons.
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._min_evidence_windows = max(1, int(cfg.get("min_evidence_windows") or DEFAULT_MIN_EVIDENCE_WINDOWS))
        self._max_change_per_step = max(0.0, float(cfg.get("max_change_per_step") or DEFAULT_MAX_CHANGE_PER_STEP))
        self._max_change_per_day = max(0.0, float(cfg.get("max_change_per_day") or DEFAULT_MAX_CHANGE_PER_DAY))
        self._max_active = max(1, int(cfg.get("max_active_experiments") or DEFAULT_MAX_ACTIVE))
        self._min_dwell_s = max(0.0, float(cfg.get("min_dwell_s") or DEFAULT_MIN_DWELL_S))
        self._cooldown_s = max(0.0, float(cfg.get("cooldown_s") or DEFAULT_COOLDOWN_S))
        self._max_per_window = max(1, int(cfg.get("max_changes_per_window") or DEFAULT_MAX_PER_WINDOW))
        context.subscribe(EVENT_PROPOSE, self._on_proposal)
        context.subscribe(EVENT_KILL, self._on_kill)

    async def start(self) -> None:
        self._running = True
        # انشر خط الأساس فور الإقلاع كي لا تبقى لوحة 850 صامتة حتى أول اقتراح.
        await self._publish_limits()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    async def _on_kill(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        # §27: when the switch is OFF every new candidate is rejected — and
        # trading is untouched (this atom never touches trading events).
        self._kill_active = bool(payload.get("adaptation_off") is True
                                 or payload.get("active") is False)
        self._kill_reason = _text(payload.get("reason"))

    async def _on_proposal(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        proposal_id = _text(payload.get("proposal_id"))
        target = _text(payload.get("target"))
        if not proposal_id or not target:
            self._drop("PROPOSAL_IDENTITY_MISSING"); return
        now = time.monotonic()
        magnitude = abs(_num(payload.get("overall_drift")) or 0.0)
        rollback_version = len(self._experiments)
        gates: list[tuple[str, bool, str]] = []

        if self._kill_active:
            gates.append(("kill_switch", False, f"ADAPTATION_OFF:{self._kill_reason}"))
        evidence_windows = int((payload.get("evidence") or {}).get("windows") or 0) \
            if isinstance(payload.get("evidence"), dict) else 0
        # drift proposals carry two evidence legs — count them as windows
        evidence_ok = evidence_windows >= self._min_evidence_windows or (
            isinstance(payload.get("evidence"), dict)
            and sum(1 for v in payload["evidence"].values() if v is not None) >= 1
            and magnitude > 0)
        gates.append(("evidence", evidence_ok, "INSUFFICIENT_EVIDENCE"))
        gates.append(("bounds", magnitude <= self._max_change_per_step * BOUNDS_HEADROOM,
                      "CHANGE_EXCEEDS_BOUNDS"))
        gates.append(("daily_limit", self._changes_today < self._max_change_per_day,
                      "DAILY_LIMIT_REACHED"))
        gates.append(("churn", len(self._window_changes) < self._max_per_window,
                      "CHURN_PROTECTION"))
        gates.append(("cooldown", now - self._last_change_at >= self._cooldown_s,
                      "CHANGE_COOLDOWN"))
        gates.append(("capacity", len(self._active) < self._max_active,
                      "MAX_ACTIVE_EXPERIMENTS"))

        passed = all(ok for _, ok, _ in gates)
        status = STATUS_SHADOW if passed else STATUS_REJECTED
        self._experiments[proposal_id] = {
            "target": target, "status": status, "magnitude": magnitude,
            "rollback_version": rollback_version, "at": now}
        if passed:
            self._active.append(proposal_id)
            self._changes_today = min(self._changes_today + magnitude, self._max_change_per_day)
            self._window_changes.append(magnitude)
            self._last_change_at = now
        await self._context.publish(EVENT_OUT, {
            "experiment_id": proposal_id, "target": target, "status": status,
            "reason": payload.get("reason"), "magnitude": round(magnitude, 4),
            "gates": [{"gate": g, "passed": ok, "code": code}
                      for g, ok, code in gates],
            "rollback_version": rollback_version,
            "max_authority": STATUS_SHADOW,  # FULL is a separate owner-gated step
            "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons)})
        await self._publish_limits()

    async def _publish_limits(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_LIMITS, {
            "max_change_per_step": self._max_change_per_step,
            "max_change_per_day": self._max_change_per_day,
            "changes_today": round(self._changes_today, 4),
            "max_active_experiments": self._max_active,
            "active": list(self._active),
            "min_dwell_s": self._min_dwell_s, "cooldown_s": self._cooldown_s,
            "changes_in_window": len(self._window_changes),
            "kill_switch_off": self._kill_active})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"experiments": len(self._experiments), "active": len(self._active),
                   "changes_today": round(self._changes_today, 4),
                   "kill_switch_off": self._kill_active,
                   "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons)}
        if not self._experiments:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_QUIET, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=f"experiments={len(self._experiments)}", details=details)
