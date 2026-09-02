# -*- coding: utf-8 -*-
"""Measurement Engine (820) — intelligence paper Phase 3.

Measures, from the live stream itself (never touching the hot path):

  * propagation latency tick -> section card (publish-to-publish via the
    bus-stamped timestamps — same honest clock the phase-0 probe uses);
  * the THREE health levels per section (§10), never mixed:
      TECHNICAL  — arrival freshness of the section's cards;
      ANALYTICAL — readiness ratio of the unified states (READY vs not);
      TRADING_UTILITY — declared UNKNOWN by name until outcomes exist
        (§9: UNKNOWN is not neutral and never a fabricated zero).

Publishes measurement.health.state + measurement.latency.state using the
ten-state vocabulary of shared/observability_contract.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.observability_contract import core_state_of

ATOM_VERSION = "1.0.0"

DEFAULT_WINDOW = 200
DEFAULT_STALE_AFTER_S = 30.0
DEFAULT_WARMING_MIN = 10
READY_HEALTHY_RATIO = 0.6
PERCENTILE_P50 = 50.0
PERCENTILE_P95 = 95.0
PERCENTILE_P99 = 99.0

EVENT_HEALTH = "measurement.health.state"
EVENT_LATENCY = "measurement.latency.state"
EVENT_TICK = "market.tick.validated"

SECTION_LIVE = {"structure.section.live": "200", "liquidity.section.live": "250",
                "stats.section.live": "300", "probability.section.live": "350",
                "strategy.section.live": "400", "analysis.section.live": "150"}

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _pct(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    import math
    idx = max(0, math.ceil(p / 100.0 * len(sorted_values)) - 1)
    return sorted_values[min(idx, len(sorted_values) - 1)]


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._window = DEFAULT_WINDOW
        self._stale_after_s = DEFAULT_STALE_AFTER_S
        self._warming_min = DEFAULT_WARMING_MIN
        self._last_tick_ts: dict[tuple[str, str, str], float] = {}
        self._latencies: dict[str, deque] = {}
        self._arrivals: dict[str, float] = {}
        self._ready_counts: dict[str, list[int]] = {}
        # Build 3: drops counted with reasons.
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._window = max(10, int(cfg.get("window") or DEFAULT_WINDOW))
        self._stale_after_s = max(1.0, float(cfg.get("stale_after_s") or DEFAULT_STALE_AFTER_S))
        self._warming_min = max(1, int(cfg.get("warming_min") or DEFAULT_WARMING_MIN))
        context.subscribe(EVENT_TICK, self._on_tick)
        for event in SECTION_LIVE:
            context.subscribe(event, self._make_section_handler(event))

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        key = (str(payload.get("account_id") or ""), str(payload.get("broker") or ""),
               str(payload.get("symbol") or ""))
        if not all(key):
            self._drop("TICK_IDENTITY_MISSING"); return
        ts = _num(payload.get("timestamp"))
        if ts is None:
            self._drop("TICK_TIMESTAMP_MISSING"); return
        self._last_tick_ts[key] = ts

    def _make_section_handler(self, event: str):
        section = SECTION_LIVE[event]

        async def handler(payload: dict[str, Any]) -> None:
            if not self._running or self._context is None or not isinstance(payload, dict):
                return
            ts = _num(payload.get("timestamp"))
            if ts is None:
                self._drop("CARD_TIMESTAMP_MISSING"); return
            now_arrival = time.monotonic()
            self._arrivals[section] = now_arrival
            unified = payload.get("unified") if isinstance(payload.get("unified"), dict) else {}
            state = str(unified.get("state") or payload.get("state") or "")
            counts = self._ready_counts.setdefault(section, [0, 0])  # [ready, total]
            counts[1] += 1
            if state.strip().upper() == "READY":
                counts[0] += 1
            # publish-to-publish latency vs the latest tick of the same scope
            key = (str(payload.get("account_id") or ""), str(payload.get("broker") or ""),
                   str(payload.get("symbol") or ""))
            tick_ts = self._last_tick_ts.get(key)
            if tick_ts is not None and ts >= tick_ts:
                delta_ms = max(0.0, (ts - tick_ts) * 1000.0)
                window = self._latencies.setdefault(section, deque(maxlen=self._window))
                window.append(delta_ms)
            await self._emit(section)

        return handler

    async def _emit(self, section: str) -> None:
        if self._context is None:
            return
        counts = self._ready_counts.get(section)
        window = self._latencies.get(section)
        arrival = self._arrivals.get(section)
        # §9 ten states — computed, never guessed.
        if counts is None or counts[1] == 0:
            state = "INSUFFICIENT_DATA"
        elif counts[1] < self._warming_min:
            state = "WARMING"
        elif arrival is None or (time.monotonic() - arrival) > self._stale_after_s:
            state = "STALE"
        else:
            state = "HEALTHY"
        ready_ratio = (counts[0] / counts[1]) if counts and counts[1] else None
        technical = "HEALTHY" if state == "HEALTHY" else state
        analytical = ("UNKNOWN" if ready_ratio is None or counts[1] < self._warming_min
                      else ("HEALTHY" if ready_ratio >= READY_HEALTHY_RATIO else "DEGRADED"))
        values = sorted(window) if window else []
        latency = {"p50_ms": _pct(values, PERCENTILE_P50), "p95_ms": _pct(values, PERCENTILE_P95),
                   "p99_ms": _pct(values, PERCENTILE_P99), "max_ms": values[-1] if values else None,
                   "samples": len(values)}
        await self._context.publish(EVENT_HEALTH, {
            "section": section, "state": state,
            "technical_health": technical,
            "analytical_health": analytical,
            "trading_utility": "UNKNOWN",  # declared by name — outcomes not wired yet
            "ready_ratio": ready_ratio, "samples": counts[1] if counts else 0,
            "core_state": core_state_of(state),
            "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons)})
        await self._context.publish(EVENT_LATENCY, {
            "section": section, **latency})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"sections": sorted(self._arrivals), "dropped": self._dropped,
                   "drop_reasons": dict(self._drop_reasons)}
        if not self._arrivals:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_INPUT, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=f"sections={len(self._arrivals)}", details=details)
