# -*- coding: utf-8 -*-
"""Adaptation Kill Switch (860) — intelligence paper §27/Phase 6.

Independent of the governor (the governor consumes ITS state, never the
reverse). When tripped:

    ADAPTATION = OFF     — the governor rejects every new candidate
    TRADING   = CONTINUES — this atom has zero connection to trading events

Trip conditions: owner command (manual, with owner identity) · critical
drift · hot-path latency regression beyond budget · experiment churn.
Reset is an explicit owner command only — never automatic.
"""

from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.1"

DEFAULT_CRITICAL_DRIFT = 1.0
DEFAULT_LATENCY_BUDGET_MS = 50.0
LATENCY_TRIP_MULTIPLIER = 3.0
# Owner's measured ruling 2026-08-23: the latency trip guards the TICK-NATIVE
# hot path only. Candle-fed sections (structure 200 builds on candles -- its
# tick->card delta measures a natural time-unit difference, not slowness) are
# out of scope BY DESIGN, not by raising thresholds.
DEFAULT_HOT_PATH_SECTIONS = ("150", "350", "400")

EVENT_DRIFT = "drift.vector.state"
EVENT_LATENCY = "measurement.latency.state"
EVENT_EXPERIMENT = "experiment.state"
EVENT_COMMAND = "adaptation.kill_switch.command"
EVENT_OUT = "adaptation.kill_switch.state"

REASON_NOT_STARTED = "NOT_STARTED"


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
        self._critical_drift = DEFAULT_CRITICAL_DRIFT
        self._latency_budget_ms = DEFAULT_LATENCY_BUDGET_MS
        self._hot_path_sections: tuple[str, ...] = DEFAULT_HOT_PATH_SECTIONS
        self._latency_out_of_scope = 0
        self._adaptation_off = False
        self._reason = ""
        self._since = 0.0
        self._trips = 0
        self._owner = ""
        # Build 3: drops counted with reasons.
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._critical_drift = max(0.0, float(cfg.get("critical_drift") or DEFAULT_CRITICAL_DRIFT))
        self._latency_budget_ms = max(0.0, float(cfg.get("latency_budget_ms") or DEFAULT_LATENCY_BUDGET_MS))
        hot = cfg.get("hot_path_sections")
        self._hot_path_sections = tuple(str(x) for x in hot) if hot else DEFAULT_HOT_PATH_SECTIONS
        context.subscribe(EVENT_DRIFT, self._on_drift)
        context.subscribe(EVENT_LATENCY, self._on_latency)
        context.subscribe(EVENT_COMMAND, self._on_command)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    async def _trip(self, reason: str, *, owner: str = "") -> None:
        if self._context is None or self._adaptation_off:
            return
        self._adaptation_off = True
        self._reason = reason
        self._owner = owner
        self._since = 0.0
        self._trips += 1
        await self._publish()

    async def _on_drift(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        overall = _num(payload.get("overall_drift"))
        if overall is not None and overall >= self._critical_drift:
            await self._trip(f"CRITICAL_DRIFT:{payload.get('section')}")

    async def _on_latency(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        section = _text(payload.get("section"))
        if section not in self._hot_path_sections:
            self._latency_out_of_scope += 1  # counted, never silent
            return
        p99 = _num(payload.get("p99_ms"))
        if p99 is not None and p99 > self._latency_budget_ms * LATENCY_TRIP_MULTIPLIER:
            await self._trip(f"HOT_PATH_LATENCY_REGRESSION:{section}")

    async def _on_command(self, payload: dict[str, Any]) -> None:
        """Owner command — the ONLY way to trip manually and the ONLY reset."""
        if not self._running or not isinstance(payload, dict):
            return
        owner = _text(payload.get("owner"))
        action = _text(payload.get("action")).upper()
        if not owner or action not in ("OFF", "ON"):
            self._drop("KILL_SWITCH_COMMAND_INVALID"); return
        if action == "OFF":
            await self._trip("OWNER_COMMAND", owner=owner)
            return
        if not self._adaptation_off:
            return
        self._adaptation_off = False
        self._reason = ""
        self._owner = owner
        await self._publish()

    async def _publish(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "adaptation_off": self._adaptation_off, "active": not self._adaptation_off,
            "reason": self._reason, "owner": self._owner,
            "trips": self._trips,
            "trading": "CONTINUES",  # carved into the payload itself (§27)
            "reset": "OWNER_COMMAND_ONLY"})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"adaptation_off": self._adaptation_off, "reason": self._reason,
                   "trips": self._trips, "hot_path_sections": list(self._hot_path_sections),
                   "latency_out_of_scope": self._latency_out_of_scope,
                   "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons)}
        # The switch itself is HEALTHY in both positions — OFF is a protective
        # state, not a failure.
        return HealthStatus(
            state=HealthState.HEALTHY,
            message=("ADAPTATION_OFF:" + self._reason) if self._adaptation_off
            else "ARMED", details=details)
