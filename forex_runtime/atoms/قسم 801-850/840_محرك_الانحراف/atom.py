# -*- coding: utf-8 -*-
"""Drift Engine (840) — intelligence paper Phase 5.

Watches the measurement stream and computes a DRIFT_VECTOR against the
established baseline (first stable windows). Proposes ONLY — it never changes
a parameter, a weight, or anything else (§17: RECALIBRATION_REQUIRED, no
automatic change).

Tracked drifts: LATENCY (p95 growth) · READINESS (ready-ratio decline) ·
REGIME churn (transition rate). Anything unmeasured is declared UNKNOWN by
name — never a zero.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.1"

DEFAULT_WARMUP_WINDOWS = 5
DEFAULT_DRIFT_THRESHOLD = 0.5
WINDOW_MEMORY = 50
DEFAULT_PROPOSAL_COOLDOWN_WINDOWS = 100

EVENT_LATENCY = "measurement.latency.state"
EVENT_HEALTH = "measurement.health.state"
EVENT_REGIME = "market.regime.state"
EVENT_OUT = "drift.vector.state"
EVENT_PROPOSE = "recalibration.proposed"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_WARMING = "BASELINE_WARMING"


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._warmup = DEFAULT_WARMUP_WINDOWS
        self._drift_threshold = DEFAULT_DRIFT_THRESHOLD
        self._latency_windows: dict[str, deque] = {}
        self._readiness_windows: dict[str, deque] = {}
        self._baseline: dict[str, dict[str, float]] = {}
        self._regime_transitions = 0
        self._proposals = 0
        self._emitted = 0
        # Owner's live measurement 2026-08-23: 13,000 wasted proposals while
        # the kill switch was off -- a feedback burn on the very path it
        # complained about. Two cures: hear the switch, and cool down.
        self._proposal_cooldown_windows = DEFAULT_PROPOSAL_COOLDOWN_WINDOWS
        self._cooldowns: dict[str, int] = {}
        self._adaptation_off = False
        self._suppressed = 0
        # Build 3: drops counted with reasons.
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._warmup = max(1, int(cfg.get("warmup_windows") or DEFAULT_WARMUP_WINDOWS))
        self._drift_threshold = max(0.0, float(cfg.get("drift_threshold") or DEFAULT_DRIFT_THRESHOLD))
        self._proposal_cooldown_windows = max(0, int(
            cfg.get("proposal_cooldown_windows") or DEFAULT_PROPOSAL_COOLDOWN_WINDOWS))
        context.subscribe(EVENT_LATENCY, self._on_latency)
        context.subscribe(EVENT_HEALTH, self._on_health)
        context.subscribe(EVENT_REGIME, self._on_regime)
        context.subscribe("adaptation.kill_switch.state", self._on_kill_switch)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    @staticmethod
    def _ratio(current: float | None, baseline: float | None,
               *, lower_is_better: bool) -> float | None:
        if current is None or baseline is None or baseline == 0:
            return None
        if lower_is_better:
            return (current - baseline) / baseline
        return (baseline - current) / baseline if baseline else None

    def _window_push(self, store: dict[str, deque], section: str, value: float) -> deque:
        window = store.setdefault(section, deque(maxlen=WINDOW_MEMORY))
        window.append(value)
        return window

    def _baseline_of(self, section: str, metric: str, window: deque) -> float | None:
        record = self._baseline.setdefault(section, {})
        if metric not in record and len(window) >= self._warmup:
            record[metric] = float(sum(window) / len(window))
        return record.get(metric)

    async def _on_latency(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        section = str(payload.get("section") or "")
        p95 = _num(payload.get("p95_ms"))
        if not section or p95 is None:
            self._drop("LATENCY_FIELD_MISSING"); return
        window = self._window_push(self._latency_windows, section, p95)
        baseline = self._baseline_of(section, "p95", window)
        if baseline is not None:
            await self._emit(section, latency_drift=self._ratio(
                p95, baseline, lower_is_better=True))

    async def _on_health(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        section = str(payload.get("section") or "")
        ready = _num(payload.get("ready_ratio"))
        if not section or ready is None:
            self._drop("READINESS_FIELD_MISSING"); return
        window = self._window_push(self._readiness_windows, section, ready)
        baseline = self._baseline_of(section, "ready", window)
        if baseline is not None:
            await self._emit(section, readiness_drift=self._ratio(
                ready, baseline, lower_is_better=False))

    async def _on_regime(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self._regime_transitions = int(payload.get("transitions") or 0)

    async def _on_kill_switch(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        # §27: while adaptation is OFF the engine keeps MEASURING but stops
        # proposing -- no wasted candidates, no feedback burn.
        self._adaptation_off = bool(payload.get("adaptation_off") is True
                                    or payload.get("active") is False)

    async def _emit(self, section: str, *, latency_drift: float | None = None,
                    readiness_drift: float | None = None) -> None:
        if self._context is None:
            return
        known = [d for d in (latency_drift, readiness_drift) if d is not None]
        overall = max(known) if known else None
        await self._context.publish(EVENT_OUT, {
            "section": section,
            "drift_vector": {"latency": latency_drift, "readiness": readiness_drift,
                             "regime_churn": None},
            "overall_drift": overall, "threshold": self._drift_threshold,
            "baseline": self._baseline.get(section, {}),
            "adaptation_off": self._adaptation_off, "suppressed": self._suppressed,
            "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons)})
        self._emitted += 1
        cooldown_now = self._cooldowns.get(section, 0)
        if cooldown_now > 0:
            self._cooldowns[section] = cooldown_now - 1
        if overall is not None and overall >= self._drift_threshold:
            if self._adaptation_off:
                self._suppressed += 1
            elif cooldown_now == 0:
                self._proposals += 1
                self._cooldowns[section] = self._proposal_cooldown_windows
                # §17: propose ONLY — the governor decides, nothing changes here.
                await self._context.publish(EVENT_PROPOSE, {
                    "target": section, "reason": "DRIFT_THRESHOLD_EXCEEDED",
                    "overall_drift": round(overall, 4),
                    "evidence": {"latency": latency_drift, "readiness": readiness_drift},
                    "proposal_id": f"DRIFT-{section}-{self._proposals}"})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"sections": sorted(self._baseline), "emitted": self._emitted,
                   "proposals": self._proposals, "suppressed": self._suppressed,
                   "adaptation_off": self._adaptation_off,
                   "cooldowns": {k: v for k, v in self._cooldowns.items() if v > 0},
                   "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons)}
        if not self._baseline:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_WARMING, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=f"sections={len(self._baseline)} proposals={self._proposals}",
                            details=details)
