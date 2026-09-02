from __future__ import annotations

from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.probability_contract import (
    BASE_MODEL_IDS,
    STATE_NOT_READY,
    STATE_READY,
    clip,
)
from shared.section_contract import section_atom

ATOM_VERSION = "2.1.0"
# v2.1.0 (2026-08-27, ported from atom 359's own 2.1.0 fix -- same shape of
# unbounded self._cycles, same fix): open cycles are BOUNDED. There was no
# expiry path at all -- any cycle missing one of the seven models stayed in
# memory forever, guaranteed growth under tick-period cycles. Oldest
# incomplete cycle is dropped past the cap, counted and declared (never
# silent). Thresholds (required_depth / confidence_threshold) untouched:
# owner dials.
_MAX_OPEN_CYCLES = 512
EVENT_OUT = "probability.merged.state"
MODEL_ID = "models_merged"
MODEL_EVENTS = (
    "probability.trend.state",
    "probability.reversal.state",
    "probability.breakout.state",
    "probability.pullback.state",
    "probability.momentum.state",
    "probability.range.state",
    "probability.hurst.state",
)


def number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


@section_atom("350", "357")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._cycles: dict[str, dict[str, dict[str, Any]]] = {}
        self._required_depth = 60.0
        self._confidence_threshold = 60.0
        self._emitted = self._invalid = self._duplicates = 0
        self._evicted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._required_depth = clip(context.config.get("required_depth", 60.0))
        self._confidence_threshold = clip(
            context.config.get("confidence_threshold", 60.0)
        )
        for event in MODEL_EVENTS:
            context.subscribe(event, self._on_model)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_model(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        cycle_id = str(payload.get("cycle_id") or "")
        model_id = str(payload.get("model_id") or payload.get("id") or "")
        if not cycle_id or model_id not in BASE_MODEL_IDS:
            self._invalid += 1
            return
        if cycle_id not in self._cycles and len(self._cycles) >= _MAX_OPEN_CYCLES:
            # v2.1.0: bounded -- drop the OLDEST incomplete cycle, counted.
            self._cycles.pop(next(iter(self._cycles)))
            self._evicted += 1
        cycle = self._cycles.setdefault(cycle_id, {})
        if model_id in cycle:
            self._duplicates += 1
            return
        cycle[model_id] = dict(payload)
        if len(cycle) == len(BASE_MODEL_IDS):
            await self._merge(cycle_id)

    async def _merge(self, cycle_id: str) -> None:
        rows = self._cycles.pop(cycle_id, None)
        if not rows or self._context is None:
            return
        first = next(iter(rows.values()))
        ready_rows = [
            row
            for row in rows.values()
            if row.get("ready") is True and number(row.get("weight_applied")) > 0
        ]
        directional = [row for row in ready_rows if number(row.get("direction")) != 0]
        available_weight = sum(number(row.get("weight")) for row in rows.values())
        active_weight = sum(number(row.get("weight_applied")) for row in ready_rows)
        directional_weight = sum(
            number(row.get("weight_applied")) for row in directional
        )

        def weighted(field: str, source: list[dict[str, Any]], mass: float) -> float:
            return (
                (
                    sum(
                        number(row.get(field)) * number(row.get("weight_applied"))
                        for row in source
                    )
                    / mass
                )
                if mass > 0
                else 0.0
            )

        direction = weighted("direction", directional, directional_weight)
        strength = weighted("strength", ready_rows, active_weight)
        confidence = weighted("confidence", ready_rows, active_weight)
        current_depth = weighted("current_depth", list(rows.values()), available_weight)
        probability = weighted("probability", ready_rows, active_weight)
        ready = (
            bool(directional)
            and current_depth >= self._required_depth
            and confidence >= self._confidence_threshold
        )
        signal = "buy" if direction > 0 else "sell" if direction < 0 else "neutral"
        state = STATE_READY if ready else STATE_NOT_READY
        await self._context.publish(
            EVENT_OUT,
            {
                "account_id": first.get("account_id"),
                "broker": first.get("broker"),
                "symbol": first.get("symbol"),
                "timeframe": "tick",
                "period_start": first.get("period_start"),
                "cycle_id": cycle_id,
                "source_timestamp": first.get("source_timestamp"),
                "sequence": first.get("sequence"),
                "id": MODEL_ID,
                "model_id": MODEL_ID,
                "analysis_mode": "live_tick",
                "contract_version": 2,
                "status": "ok",
                "signal": signal,
                "direction": round(direction, 6),
                "score": round(direction, 6),
                "strength": round(strength, 6),
                "confidence": round(confidence, 6),
                "probability": round(probability, 8),
                "current_depth": round(current_depth, 6),
                "required_depth": round(self._required_depth, 6),
                "confidence_threshold": round(self._confidence_threshold, 6),
                "threshold": round(self._confidence_threshold, 6),
                "weight": 0.0,
                "weight_applied": 0.0,
                "ratio": 0.0,
                "ready": ready,
                "analysis_state": state,
                "state": state,
                "active_weight": round(active_weight, 6),
                "available_weight": round(available_weight, 6),
                "missing_weight": round(max(0.0, available_weight - active_weight), 6),
                "quality": "good" if ready else "low",
                "warnings": [] if ready else ["MERGED_MODELS_NOT_READY"],
                "metadata": {
                    "method": "weighted_live_tick_v2",
                    "models": {
                        key: {
                            "ready": value.get("ready"),
                            "direction": value.get("direction"),
                            "confidence": value.get("confidence"),
                            "weight": value.get("weight"),
                        }
                        for key, value in rows.items()
                    },
                },
            },
        )
        self._emitted += 1

    async def snapshot(self) -> dict[str, Any]:
        return {
            "cycles": self._cycles,
            "emitted": self._emitted,
            "invalid": self._invalid,
            "duplicates": self._duplicates,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            cycles = state.get("cycles", {})
            self._cycles = cycles if isinstance(cycles, dict) else {}
            self._emitted = int(state.get("emitted", 0))
            self._invalid = int(state.get("invalid", 0))
            self._duplicates = int(state.get("duplicates", 0))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="merged=%d open=%d evicted=%d" % (
                self._emitted, len(self._cycles), self._evicted),
            details={
                "emitted": self._emitted,
                "open": len(self._cycles),
                "invalid": self._invalid,
                "duplicates": self._duplicates,
                "evicted": self._evicted,
            },
        )
