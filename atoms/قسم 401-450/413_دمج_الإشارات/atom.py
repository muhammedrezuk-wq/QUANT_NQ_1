from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import (
    ALL_IDS,
    CONTEXT_IDS,
    DIRECTIONAL_IDS,
    STATE_NOT_READY,
    STATE_READY,
    clip,
)

ATOM_VERSION = "2.2.0"
EVENT_OUT = "strategy.aggregate.state"
AGGREGATE_ID = "strategy_aggregate"
EVENTS = (
    "strategy.trend.state",
    "strategy.reversal.state",
    "strategy.breakout.state",
    "strategy.pullback.state",
    "strategy.momentum.state",
    "strategy.range.state",
    "strategy.liquidity.state",
    "strategy.entry_quality.state",
    "strategy.invalidation_quality.state",
    "strategy.news.state",
    "strategy.session.state",
)


def num(v):
    try:
        r = float(v)
    except (TypeError, ValueError):
        return 0.0
    return r if r == r else 0.0


def measured(v):
    # A8: a real measurement or None -- never a silent 0 fallback.
    try:
        r = float(v)
    except (TypeError, ValueError):
        return None
    return r if r == r else None


@section_atom("400", "413")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._cycles = {}
        self._required_depth = 60.0
        self._confidence_threshold = 60.0
        self._min_active_weight = 40.0
        self._emitted = self._invalid = self._duplicates = 0

    async def initialize(self, c):
        self._context = c
        self._required_depth = clip(c.config.get("required_depth", 60))
        self._confidence_threshold = clip(c.config.get("confidence_threshold", 60))
        self._min_active_weight = clip(c.config.get("min_active_weight", 40))
        for event in EVENTS:
            c.subscribe(event, self._on_component)

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    async def _on_component(self, p: dict[str, Any]):
        if not self._running or self._context is None or not isinstance(p, dict):
            return
        cid = str(p.get("cycle_id") or "")
        sid = str(p.get("strategy_id") or p.get("id") or "")
        if not cid or sid not in ALL_IDS:
            self._invalid += 1
            return
        rows = self._cycles.setdefault(cid, {})
        if sid in rows:
            self._duplicates += 1
            return
        rows[sid] = dict(p)
        if len(rows) == len(ALL_IDS):
            await self._aggregate(cid)

    async def _aggregate(self, cid):
        rows = self._cycles.pop(cid, None)
        if not rows or self._context is None:
            return
        first = next(iter(rows.values()))
        # v2.2.0 (2026-08-25, sealed-log item 47 "the impossible gate"): the
        # context factor multiplies in ONLY what a READY context strategy
        # actually MEASURED. The old code read every absent/not-ready value
        # through num() as 0.0 -- a manufactured zero that capped the whole
        # aggregate at ~0.36 x weights, mathematically below the owner's
        # min_active_weight dial (measured max 24.1 vs 40.0 across 186
        # samples => 451 complete=0 in 100%). An unmeasured modulator is now
        # EXCLUDED and DECLARED in context_unmeasured -- never a silent cap.
        context_factor = 1.0
        context_unmeasured = []
        contexts = [rows[x] for x in CONTEXT_IDS]
        for x in contexts:
            cf = measured(x.get("context_factor"))
            if x.get("ready") is True and cf is not None:
                context_factor = min(context_factor, max(0.0, min(1.0, cf)))
            else:
                context_unmeasured.append(
                    str(x.get("strategy_id") or x.get("id") or ""))
        entry = rows["entry_structure_quality"]
        invalidation = rows["invalidation_quality"]
        for quality_row in (entry, invalidation):
            strength_value = measured(quality_row.get("strength"))
            if quality_row.get("ready") is True and strength_value is not None:
                context_factor *= max(0.0, min(1.0, strength_value / 100))
            else:
                context_unmeasured.append(
                    str(quality_row.get("strategy_id") or quality_row.get("id") or ""))
        directional = [rows[x] for x in DIRECTIONAL_IDS]
        available = sum(num(x.get("weight")) for x in directional)
        effective = [
            (x, num(x.get("weight_applied")) * context_factor)
            for x in directional
            if x.get("ready") is True
        ]
        active = sum(w for _, w in effective)

        # Unit 150 closure, phase 1 (owner order 2026-08-23): an aggregate with
        # NO ready contributor has an UNDEFINED confidence, not a measured low
        # one. The old code answered 0.0 here -- a silent lie that read as
        # "measured and very low" downstream. Undefined is declared: confidence
        # passes as None, confidence_defined says why, and READY is withheld.
        undefined = active <= 0

        def weighted(field):
            if undefined:
                return None
            return sum(num(x.get(field)) * w for x, w in effective) / active

        direction = weighted("direction")
        strength = weighted("strength")
        confidence = weighted("confidence")
        depth = (
            sum(num(x.get("current_depth")) * num(x.get("weight")) for x in directional)
            / available
            if available > 0
            else 0.0
        )
        direction_value = direction if direction is not None else 0.0
        strength_value = strength if strength is not None else 0.0
        readiness = (
            round(min(100.0, depth / self._required_depth * 100.0), 1)
            if self._required_depth > 0
            else None
        )
        ready = (
            not undefined
            and active >= self._min_active_weight
            and depth >= self._required_depth
            and confidence is not None
            and confidence >= self._confidence_threshold
            and direction_value != 0
        )
        state = STATE_READY if ready else STATE_NOT_READY
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
                "id": AGGREGATE_ID,
                "strategy_id": AGGREGATE_ID,
                "analysis_mode": "live_tick",
                "contract_version": 2,
                "status": "ok",
                "signal": (
                    "positive_strategic_lean"
                    if direction_value > 0
                    else (
                        "negative_strategic_lean"
                        if direction_value < 0
                        else "balanced_strategic_context"
                    )
                ),
                "direction": round(direction_value, 6),
                "score": round(direction_value, 6),
                "strength": round(strength_value, 6),
                "confidence": round(confidence, 6) if confidence is not None else None,
                "confidence_defined": confidence is not None,
                "readiness_pct": readiness,
                "current_depth": round(depth, 6),
                "required_depth": round(self._required_depth, 6),
                "confidence_threshold": round(self._confidence_threshold, 6),
                "threshold": round(self._confidence_threshold, 6),
                "weight": 0.0,
                "weight_applied": 0.0,
                "ratio": 0.0,
                "ready": ready,
                "analysis_state": state,
                "state": state,
                "available_weight": round(available, 6),
                "active_weight": round(active, 6),
                "missing_weight": round(max(0.0, available - active), 6),
                "context_factor": round(context_factor, 6),
                "context_unmeasured": context_unmeasured,
                "quality": "good" if ready else "low",
                "warnings": (
                    ([] if ready else
                     (["NO_READY_CONTRIBUTOR"] if undefined
                      else ["STRATEGY_AGGREGATE_NOT_READY"]))
                    + (["CONTEXT_PARTIAL"] if context_unmeasured else [])
                ),
                "metadata": {
                    "method": "weighted_descriptive_strategy_v2",
                    "components": {
                        k: {
                            "direction": v.get("direction"),
                            "strength": v.get("strength"),
                            "confidence": v.get("confidence"),
                            "ready": v.get("ready"),
                            "weight": v.get("weight"),
                        }
                        for k, v in rows.items()
                    },
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

    async def restore(self, x):
        if isinstance(x, dict):
            self._cycles = (
                x.get("cycles", {}) if isinstance(x.get("cycles", {}), dict) else {}
            )
            self._emitted = int(x.get("emitted", 0))
            self._invalid = int(x.get("invalid", 0))
            self._duplicates = int(x.get("duplicates", 0))

    async def health_check(self):
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="aggregates=%d" % self._emitted,
            details={
                "emitted": self._emitted,
                "open": len(self._cycles),
                "invalid": self._invalid,
                "duplicates": self._duplicates,
            },
        )
