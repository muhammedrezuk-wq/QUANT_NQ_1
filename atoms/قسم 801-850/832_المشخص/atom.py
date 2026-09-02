# -*- coding: utf-8 -*-
"""The System Diagnostician (832) -- the missing organ of the intelligence
block, built on the owner's word (2026-08-26) under his AI constitution v2.1.

It synthesises what the living eyes already measure -- alerts (831), section
health/latency (820), market regime (830), drift (840), the adaptation kill
switch (860) and decision behaviour (811) -- into ONE explained picture:

    state          SS4  : UNKNOWN / OBSERVING / HEALTHY / DEGRADED
    attribution    SS8/13: MARKET / SYSTEM / DATA / EXECUTION / UNKNOWN
    because        SS37 : every state ships its reasons, never a bare badge

Measurement only. Zero decision paths, zero parameter writes, zero trading
events -- exactly phase 1 (Monitor) of the constitution. UNKNOWN is a state
(SS41): missing evidence is declared, never converted to zero or to safe.
"""
from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_ALERTS = "system.alert.state"
EVENT_SECTION_HEALTH = "measurement.health.state"
EVENT_LATENCY = "measurement.latency.state"
EVENT_REGIME = "market.regime.state"
EVENT_DRIFT = "drift.vector.state"
EVENT_KILL = "adaptation.kill_switch.state"
EVENT_BEHAVIOUR = "decision.behaviour.telemetry"
EVENT_TIME = "SYS_SECOND"
EVENT_OUT = "system.diagnosis.state"

STATE_UNKNOWN = "UNKNOWN"
STATE_OBSERVING = "OBSERVING"
STATE_HEALTHY = "HEALTHY"
STATE_DEGRADED = "DEGRADED"

CAUSE_MARKET = "MARKET"
CAUSE_SYSTEM = "SYSTEM"
CAUSE_DATA = "DATA"
CAUSE_EXECUTION = "EXECUTION"
CAUSE_UNKNOWN = "UNKNOWN"

#: SS50 -- when causes compete, the closer-to-the-wire one leads.
_CAUSE_RANK = (CAUSE_DATA, CAUSE_SYSTEM, CAUSE_EXECUTION, CAUSE_MARKET)

#: Alert-event name fragments -> attribution domain (declared heuristics,
#: SS38: the alert layer stays the source of facts; this only classifies).
_ALERT_DOMAINS = (
    ("market_data", CAUSE_DATA), ("feed", CAUSE_DATA), ("symbol", CAUSE_DATA),
    ("time", CAUSE_DATA), ("validation", CAUSE_DATA),
    ("persistence", CAUSE_SYSTEM), ("backup", CAUSE_SYSTEM),
    ("integrity", CAUSE_SYSTEM), ("archive", CAUSE_SYSTEM),
    ("cleanup", CAUSE_SYSTEM),
    ("trade", CAUSE_EXECUTION), ("execution", CAUSE_EXECUTION),
    ("order", CAUSE_EXECUTION),
    ("probability", CAUSE_MARKET), ("regime", CAUSE_MARKET),
)

_MIN_DOMAINS_FOR_VERDICT = 3
_PUBLISH_EVERY_S = 5.0
_SEVERITY_HOT = ("CRITICAL", "HIGH")


def _text(value: Any) -> str:
    return str(value or "").strip()


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._alerts: dict[str, Any] | None = None
        self._sections: dict[str, dict[str, Any]] = {}
        self._latency: dict[str, dict[str, Any]] = {}
        self._drift: dict[str, dict[str, Any]] = {}
        self._regime: dict[str, Any] | None = None
        self._regime_changed = False
        self._kill: dict[str, Any] | None = None
        self._silent: dict[str, bool] = {}
        self._dirty = False
        self._since_publish = 0.0
        self._emitted = 0
        self._seen = 0
        self._last: dict[str, Any] | None = None

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_ALERTS, self._on_alerts)
        context.subscribe(EVENT_SECTION_HEALTH, self._on_section)
        context.subscribe(EVENT_LATENCY, self._on_latency)
        context.subscribe(EVENT_REGIME, self._on_regime)
        context.subscribe(EVENT_DRIFT, self._on_drift)
        context.subscribe(EVENT_KILL, self._on_kill)
        context.subscribe(EVENT_BEHAVIOUR, self._on_behaviour)
        context.subscribe(EVENT_TIME, self._on_second)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _mark(self) -> None:
        self._seen += 1
        self._dirty = True

    async def _on_alerts(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            self._alerts = payload
            self._mark()

    async def _on_section(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict) and payload.get("section"):
            self._sections[_text(payload["section"])] = payload
            self._mark()

    async def _on_latency(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict) and payload.get("section"):
            self._latency[_text(payload["section"])] = payload
            self._mark()

    async def _on_regime(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        previous = (self._regime or {}).get("regime")
        self._regime = payload
        self._regime_changed = bool(previous and payload.get("regime") != previous)
        self._mark()

    async def _on_drift(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict) and payload.get("section"):
            self._drift[_text(payload["section"])] = payload
            self._mark()

    async def _on_kill(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            self._kill = payload
            self._mark()

    async def _on_behaviour(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = _text(payload.get("symbol"))
        if symbol:
            self._silent[symbol] = payload.get("all_neutral") is True
            self._mark()

    async def _on_second(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        self._since_publish += 1.0
        if not self._dirty or self._since_publish < _PUBLISH_EVERY_S:
            return
        self._since_publish = 0.0
        self._dirty = False
        self._last = self._diagnose()
        self._emitted += 1
        await self._context.publish(EVENT_OUT, self._last)

    # ------------------------------------------------------------------ SS4+SS13
    def _facts(self) -> list[dict[str, str]]:
        facts: list[dict[str, str]] = []
        alerts = (self._alerts or {}).get("alerts")
        if isinstance(alerts, dict):
            for event, record in alerts.items():
                severity = _text((record or {}).get("severity")).upper()
                if severity not in _SEVERITY_HOT:
                    continue
                domain = CAUSE_SYSTEM
                lowered = _text(event).lower()
                for fragment, mapped in _ALERT_DOMAINS:
                    if fragment in lowered:
                        domain = mapped
                        break
                facts.append({"domain": domain, "source": "831",
                              "fact": "إنذار %s: %s" % (severity, event)})
        for section, row in self._sections.items():
            technical = _text(row.get("technical_health")).upper()
            if technical and technical not in ("OK", "HEALTHY", "GOOD"):
                facts.append({"domain": CAUSE_SYSTEM, "source": "820",
                              "fact": "قسم %s صحته التقنية %s" % (section, technical)})
        for section, row in self._drift.items():
            overall = row.get("overall_drift")
            threshold = row.get("threshold")
            try:
                if overall is not None and threshold is not None \
                        and float(overall) >= float(threshold):
                    facts.append({"domain": CAUSE_SYSTEM, "source": "840",
                                  "fact": "انحراف قسم %s بلغ %.3f (حده %.3f)"
                                          % (section, float(overall), float(threshold))})
            except (TypeError, ValueError):
                continue
        if self._kill is not None and (self._kill.get("adaptation_off") is True
                                       or self._kill.get("active") is False):
            facts.append({"domain": CAUSE_SYSTEM, "source": "860",
                          "fact": "التكيف مقطوع: %s" % _text(self._kill.get("reason"))})
        if self._regime_changed and self._regime is not None:
            facts.append({"domain": CAUSE_MARKET, "source": "830",
                          "fact": "حالة السوق تحولت %s ← %s"
                                  % (_text(self._regime.get("previous_regime")),
                                     _text(self._regime.get("regime")))})
        return facts

    def _diagnose(self) -> dict[str, Any]:
        domains_present = sum(1 for seen in (
            self._alerts is not None, bool(self._sections), self._regime is not None,
            bool(self._drift), self._kill is not None) if seen)
        facts = self._facts()
        degrading = [f for f in facts if f["domain"] != CAUSE_MARKET]
        if domains_present == 0:
            state, primary, secondary, confidence = STATE_UNKNOWN, CAUSE_UNKNOWN, None, 0.0
        elif domains_present < _MIN_DOMAINS_FOR_VERDICT:
            state, primary, secondary, confidence = STATE_OBSERVING, CAUSE_UNKNOWN, None, 0.0
        elif not degrading:
            state, primary, secondary, confidence = STATE_HEALTHY, CAUSE_UNKNOWN, None, 0.0
        else:
            state = STATE_DEGRADED
            counts: dict[str, int] = {}
            for fact in degrading:
                counts[fact["domain"]] = counts.get(fact["domain"], 0) + 1
            ranked = sorted(counts, key=lambda d: (-counts[d], _CAUSE_RANK.index(d)))
            primary = ranked[0]
            secondary = ranked[1] if len(ranked) > 1 else None
            share = counts[primary] / max(1, len(degrading))
            confidence = round(0.9 * share if counts[primary] > 1 else 0.6 * share, 2)
        regime = self._regime or {}
        because = [f["fact"] for f in facts] or ["كل المصادر ضمن حدودها"]
        return {
            "id": "diagnosis", "state": state,
            "primary_cause": primary, "secondary_cause": secondary,
            "confidence": confidence,
            "because": because, "facts": facts,
            "regime": {"now": regime.get("regime"),
                       "previous": regime.get("previous_regime"),
                       "confidence": regime.get("regime_confidence")},
            "adaptation_off": bool(self._kill is not None
                                   and (self._kill.get("adaptation_off") is True
                                        or self._kill.get("active") is False)),
            "adaptation_reason": _text((self._kill or {}).get("reason")),
            "silent_scopes": sorted(s for s, silent in self._silent.items() if silent),
            "sections_seen": sorted(self._sections),
            "inputs_present": domains_present,
        }

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "emitted": self._emitted, "seen": self._seen}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._emitted = int(state.get("emitted") or 0)
            self._seen = int(state.get("seen") or 0)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        verdict = self._last or self._diagnose()
        details = {"seen": self._seen, "emitted": self._emitted,
                   "diagnosis": verdict}
        if verdict["state"] in (STATE_UNKNOWN, STATE_OBSERVING):
            return HealthStatus(state=HealthState.DEGRADED,
                                message=verdict["state"], details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="%s cause=%s" % (verdict["state"], verdict["primary_cause"]),
            details=details)
