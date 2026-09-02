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
# v2.1.0 (2026-08-25): open cycles are BOUNDED. There was no expiry path at
# all -- any cycle missing one of the seven models stayed in memory forever,
# guaranteed growth under tick-period cycles. Oldest incomplete cycle is
# dropped past the cap, counted and declared (never silent). Thresholds
# (min_confidence / min_ready_coverage) untouched: owner dials.
_MAX_OPEN_CYCLES = 512
EVENT_OUT = "probability.confidence.state"
MODEL_ID = "confidence_aggregator"
MODEL_EVENTS = (
    "probability.trend.state",
    "probability.reversal.state",
    "probability.breakout.state",
    "probability.pullback.state",
    "probability.momentum.state",
    "probability.range.state",
    "probability.hurst.state",
)


def num(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


@section_atom("350", "359")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._cycles = {}
        self._min_conf = 60.0
        self._min_coverage = 60.0
        self._emitted = 0
        self._invalid = 0
        self._duplicates = 0
        self._evicted = 0

    async def initialize(self, context):
        self._context = context
        self._min_conf = clip(context.config.get("min_confidence", 60.0))
        self._min_coverage = clip(context.config.get("min_ready_coverage", 60.0))
        for event in MODEL_EVENTS:
            context.subscribe(event, self._on_model)

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    async def _on_model(self, payload: dict[str, Any]):
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        cid = str(payload.get("cycle_id") or "")
        mid = str(payload.get("model_id") or payload.get("id") or "")
        if not cid or mid not in BASE_MODEL_IDS:
            self._invalid += 1
            return
        if cid not in self._cycles and len(self._cycles) >= _MAX_OPEN_CYCLES:
            # v2.1.0: bounded -- drop the OLDEST incomplete cycle, counted.
            self._cycles.pop(next(iter(self._cycles)))
            self._evicted += 1
        cycle = self._cycles.setdefault(cid, {})
        if mid in cycle:
            self._duplicates += 1
            return
        cycle[mid] = dict(payload)
        if len(cycle) == len(BASE_MODEL_IDS):
            await self._emit(cid)

    async def _emit(self, cid: str):
        rows = self._cycles.pop(cid, None)
        if not rows or self._context is None:
            return
        first = next(iter(rows.values()))
        expected = len(BASE_MODEL_IDS)
        ready = [row for row in rows.values() if row.get("ready") is True]
        coverage = len(ready) / expected
        mean = (
            sum(num(row.get("confidence")) for row in ready) / len(ready)
            if ready
            else 0.0
        )
        overall = mean
        probability = (
            sum(num(row.get("probability")) for row in rows.values()) / expected
        )
        depth = sum(num(row.get("current_depth")) for row in rows.values()) / expected
        is_ready = coverage * 100.0 >= self._min_coverage and overall >= self._min_conf
        state = STATE_READY if is_ready else STATE_NOT_READY
        await self._context.publish(
            EVENT_OUT,
            {
                "account_id": first.get("account_id"),
                "broker": first.get("broker"),
                "symbol": first.get("symbol"),
                "timeframe": "tick",
                "period_start": first.get("period_start"),
                "cycle_id": cid,
                "source_timestamp": first.get("source_timestamp"),
                "sequence": first.get("sequence"),
                "id": MODEL_ID,
                "model_id": MODEL_ID,
                "analysis_mode": "live_tick",
                "contract_version": 2,
                "status": "ok",
                "signal": "high_confidence" if is_ready else "low_confidence",
                "direction": 0.0,
                "score": 0.0,
                "strength": round(overall, 6),
                "confidence": round(overall, 6),
                "probability": round(probability, 8),
                "current_depth": round(depth, 6),
                "required_depth": 60.0,
                "confidence_threshold": round(self._min_conf, 6),
                "threshold": round(self._min_conf, 6),
                "weight": 0.0,
                "weight_applied": 0.0,
                "ratio": 0.0,
                "ready": is_ready,
                "analysis_state": state,
                "state": state,
                "coverage": round(coverage * 100.0, 6),
                "quality": "good" if is_ready else "low",
                "warnings": [] if is_ready else ["CONFIDENCE_PANEL_NOT_READY"],
                "metadata": {
                    "method": "weighted_confidence_live_tick_v2",
                    "present": len(rows),
                    "ready_models": len(ready),
                },
            },
        )
        self._emitted += 1

    async def snapshot(self):
        return {
            "cycles": self._cycles,
            "emitted": self._emitted,
            "invalid": self._invalid,
            "duplicates": self._duplicates,
        }

    async def restore(self, state):
        if isinstance(state, dict):
            self._cycles = (
                state.get("cycles", {})
                if isinstance(state.get("cycles", {}), dict)
                else {}
            )
            self._emitted = int(state.get("emitted", 0))
            self._invalid = int(state.get("invalid", 0))
            self._duplicates = int(state.get("duplicates", 0))

    async def health_check(self):
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="confidence_cycles=%d open=%d evicted=%d" % (
                self._emitted, len(self._cycles), self._evicted),
            details={
                "emitted": self._emitted,
                "open": len(self._cycles),
                "invalid": self._invalid,
                "duplicates": self._duplicates,
                "evicted": self._evicted,
            },
        )
