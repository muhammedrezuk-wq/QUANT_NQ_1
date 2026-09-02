# -*- coding: utf-8 -*-
"""Market Regime Engine (830) — intelligence paper Phase 4.

A state carrier, NEVER a trading signal (§12/§14): it publishes the regime
state and its transitions with hysteresis — it does not touch weights or
parameters, directly or indirectly.

Inputs (existing live events, nothing new):
  * structure.trend.state (201) — trend direction value
  * analysis.volatility.state (153) — volatility percent

Hysteresis (§13): ENTRY_THRESHOLD / EXIT_THRESHOLD / CONFIRMATION_WINDOW /
MIN_DURATION_S / TRANSITION — no flip-flopping on every jitter.
"""

from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

DEFAULT_ENTRY_THRESHOLD = 70.0
DEFAULT_EXIT_THRESHOLD = 45.0
DEFAULT_CONFIRMATION_WINDOW = 5
DEFAULT_MIN_DURATION_S = 60.0
DEFAULT_VOLATILITY_HIGH = 75.0
TREND_FLOOR = -100.0
TREND_CEILING = 100.0
VOLATILITY_CEILING = 100.0
CONFIDENCE_FULL_AT_SAMPLES = 20

EVENT_OUT = "market.regime.state"
EVENT_TREND = "structure.trend.state"
EVENT_VOLATILITY = "analysis.volatility.state"

REGIME_TRENDING = "TRENDING"
REGIME_RANGING = "RANGING"
REGIME_TRANSITION = "TRANSITION"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_REGIME_INPUT_YET"


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
        self._entry = DEFAULT_ENTRY_THRESHOLD
        self._exit = DEFAULT_EXIT_THRESHOLD
        self._confirm = DEFAULT_CONFIRMATION_WINDOW
        self._min_duration_s = DEFAULT_MIN_DURATION_S
        self._vol_high = DEFAULT_VOLATILITY_HIGH
        self._trend: float | None = None
        self._volatility: float | None = None
        self._regime = REGIME_RANGING
        self._previous = REGIME_RANGING
        self._since = 0.0
        self._confirmations = 0
        self._transition_target: str | None = None
        self._transitions = 0
        self._samples = 0
        # Build 3: drops counted with reasons.
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._entry = max(0.0, float(cfg.get("entry_threshold") or DEFAULT_ENTRY_THRESHOLD))
        self._exit = max(0.0, float(cfg.get("exit_threshold") or DEFAULT_EXIT_THRESHOLD))
        self._confirm = max(1, int(cfg.get("confirmation_window") or DEFAULT_CONFIRMATION_WINDOW))
        self._min_duration_s = max(0.0, float(cfg.get("min_duration_s") or DEFAULT_MIN_DURATION_S))
        self._vol_high = max(0.0, float(cfg.get("volatility_high") or DEFAULT_VOLATILITY_HIGH))
        context.subscribe(EVENT_TREND, self._on_trend)
        context.subscribe(EVENT_VOLATILITY, self._on_volatility)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    def _label(self, trending: bool) -> str:
        if not trending:
            return REGIME_RANGING
        return "TRENDING_HIGH_VOL" if (self._volatility or 0.0) >= self._vol_high else REGIME_TRENDING

    async def _evaluate(self, now: float) -> None:
        if self._context is None or self._trend is None:
            return
        self._samples += 1
        strength = abs(self._trend)
        target = self._label(strength >= self._entry)
        current = self._regime
        if current in (REGIME_TRANSITION,):
            current = self._transition_target or REGIME_RANGING
        if target != current and self._transition_target != target:
            if (now - self._since) < self._min_duration_s:
                return  # §13 minimum dwell — no flip-flop
            self._transition_target = target
            self._confirmations = 0
        if self._transition_target is not None:
            # confirmation window: the exit side re-enters before switching
            exit_threshold = self._exit if self._transition_target == REGIME_RANGING else self._entry
            strength_ok = (strength < exit_threshold if self._transition_target == REGIME_RANGING
                           else strength >= exit_threshold)
            self._confirmations = self._confirmations + 1 if strength_ok else 0
            if self._confirmations >= self._confirm:
                self._previous = self._regime
                self._regime = self._transition_target
                self._transition_target = None
                self._since = now
                self._transitions += 1
        await self._publish(now)

    async def _publish(self, now: float) -> None:
        transition_score = (self._confirmations / self._confirm
                            if self._transition_target is not None and self._confirm else 0.0)
        confidence = min(1.0, self._samples / CONFIDENCE_FULL_AT_SAMPLES)
        await self._context.publish(EVENT_OUT, {
            "regime": self._regime if self._transition_target is None else REGIME_TRANSITION,
            "previous_regime": self._previous,
            "transition_target": self._transition_target,
            "transition_score": round(transition_score, 4),
            "regime_start": self._since, "regime_confidence": round(confidence, 4),
            "regime_version": ATOM_VERSION,
            "vector": {"trend": self._trend, "volatility": self._volatility},
            "transitions": self._transitions, "samples": self._samples,
            # §14 reminder carved into the payload itself:
            "authority": "OBSERVATION_ONLY — no weight or parameter authority"})

    async def _on_trend(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        import time
        value = _num(payload.get("direction")) if _num(payload.get("direction")) is not None \
            else _num(payload.get("score"))
        if value is None:
            self._drop("TREND_VALUE_MISSING"); return
        self._trend = max(TREND_FLOOR, min(TREND_CEILING, value))
        await self._evaluate(time.time())

    async def _on_volatility(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        value = _num(payload.get("score")) if _num(payload.get("score")) is not None \
            else _num(payload.get("confidence"))
        if value is None:
            self._drop("VOLATILITY_VALUE_MISSING"); return
        self._volatility = max(0.0, min(VOLATILITY_CEILING, value))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"regime": self._regime, "transitions": self._transitions,
                   "samples": self._samples, "dropped": self._dropped,
                   "drop_reasons": dict(self._drop_reasons)}
        if self._trend is None:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_INPUT, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=f"regime={self._regime}", details=details)
