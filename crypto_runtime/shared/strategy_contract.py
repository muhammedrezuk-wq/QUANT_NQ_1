"""Live descriptive strategy contract for section 400.

This module never emits a trading decision.  Direction is a measured strategic
lean on -100..100; weights become effective only after evidence gates pass.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from shared.tick_contract import as_validated_tick

STATE_NOT_READY = "NOT_READY"
STATE_READY = "READY"
DIRECTIONAL_IDS = (
    "trend_continuation",
    "reversal_potential",
    "breakout_acceptance",
    "pullback_quality",
    "momentum_expansion",
    "range_rotation",
    "liquidity_raid",
)
CONTEXT_IDS = (
    "entry_structure_quality",
    "invalidation_quality",
    "news_regime",
    "session_regime",
)
ALL_IDS = DIRECTIONAL_IDS + CONTEXT_IDS
EQUAL_WEIGHT = 100.0 / len(DIRECTIONAL_IDS)


def finite(value: Any, fallback: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def clip(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    number = finite(value, 0.0) or 0.0
    return max(low, min(high, number))


@dataclass
class StrategicTickState:
    prices: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    bids: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    asks: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    returns: deque[float] = field(default_factory=lambda: deque(maxlen=255))
    sequence: int = 0


class StrategyRuntime:
    def __init__(self, strategy_id: str, *, directional: bool = True) -> None:
        if strategy_id not in ALL_IDS:
            raise ValueError("UNKNOWN_STRATEGY_COMPONENT")
        self.strategy_id = strategy_id
        self.directional = directional
        self.states: dict[tuple[str, str, str], StrategicTickState] = {}
        self.weight = EQUAL_WEIGHT if directional else 0.0
        self.required_depth = 60.0
        self.confidence_threshold = 60.0
        self.strength_threshold = 0.0
        self.required_ticks = 24
        self.invalid = 0

    def configure(self, config: dict[str, Any]) -> None:
        self.weight = (
            clip(config.get("weight", self.weight)) if self.directional else 0.0
        )
        self.required_depth = clip(config.get("required_depth", 60.0))
        self.confidence_threshold = clip(config.get("confidence_threshold", 60.0))
        self.strength_threshold = clip(config.get("strength_threshold", 0.0))
        self.required_ticks = max(2, int(config.get("required_ticks", 24)))

    def ingest(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], StrategicTickState] | None:
        tick = as_validated_tick(payload)
        account = str(tick.get("account_id") or "").strip()
        broker = str(tick.get("broker") or "").strip()
        symbol = str(tick.get("symbol") or "").strip().upper()
        price = finite(tick.get("price"))
        bid = finite(tick.get("bid"), price)
        ask = finite(tick.get("ask"), price)
        stamp = finite(tick.get("source_timestamp", tick.get("timestamp")))
        if (
            not account
            or not broker
            or not symbol
            or price is None
            or bid is None
            or ask is None
            or stamp is None
            or price <= 0
            or bid <= 0
            or ask < bid
            or stamp <= 0
        ):
            self.invalid += 1
            return None
        scope = (account, broker, symbol)
        state = self.states.setdefault(scope, StrategicTickState())
        if state.timestamps and stamp <= state.timestamps[-1]:
            self.invalid += 1
            return None
        if state.prices:
            state.returns.append(price / state.prices[-1] - 1.0)
        state.prices.append(price)
        state.bids.append(bid)
        state.asks.append(ask)
        state.timestamps.append(stamp)
        state.sequence += 1
        return tick, state

    def depth(self, state: StrategicTickState) -> tuple[float, dict[str, float]]:
        returns = list(state.returns)[-self.required_ticks :]
        sample = clip(len(returns) / self.required_ticks * 100.0)
        movement = clip(sum(abs(x) for x in returns) * 160_000.0)
        spreads = [
            (a - b) / p
            for b, a, p in zip(
                list(state.bids)[-self.required_ticks :],
                list(state.asks)[-self.required_ticks :],
                list(state.prices)[-self.required_ticks :],
            )
            if p > 0
        ]
        spread_quality = clip(
            100.0 - (sum(spreads) / len(spreads) if spreads else 0.0) * 200_000.0
        )
        if returns:
            mean = sum(returns) / len(returns)
            variance = sum((x - mean) ** 2 for x in returns) / len(returns)
            mean_abs = sum(abs(x) for x in returns) / len(returns)
            consistency = clip(100.0 - math.sqrt(variance) / max(mean_abs, 1e-9) * 50.0)
        else:
            consistency = 0.0
        current = clip(
            0.4 * sample + 0.25 * movement + 0.2 * spread_quality + 0.15 * consistency
        )
        return current, {
            "sample": sample,
            "movement": movement,
            "spread_quality": spread_quality,
            "consistency": consistency,
        }

    def card(
        self,
        tick: dict[str, Any],
        state: StrategicTickState,
        *,
        direction: float,
        strength: float,
        confidence: float,
        signal: str,
        evidence: dict[str, Any] | None = None,
        status: str = "ok",
        context_factor: float = 1.0,
    ) -> dict[str, Any]:
        depth, depth_parts = self.depth(state)
        direction = max(-100.0, min(100.0, float(direction)))
        strength = clip(strength)
        confidence = clip(confidence)
        context_factor = max(0.0, min(1.0, float(context_factor)))
        ready = (
            status == "ok"
            and depth >= self.required_depth
            and confidence >= self.confidence_threshold
            and strength >= self.strength_threshold
            and (not self.directional or direction != 0.0)
        )
        effective = self.weight * context_factor if ready else 0.0
        contract_state = STATE_READY if ready else STATE_NOT_READY
        return {
            "account_id": tick.get("account_id"),
            "broker": tick.get("broker"),
            "symbol": tick.get("symbol"),
            "timeframe": "tick",
            "period_start": tick.get("period_start"),
            "cycle_id": tick.get("cycle_id"),
            "source_timestamp": tick.get("source_timestamp", tick.get("timestamp")),
            "timestamp": tick.get("timestamp"),
            "sequence": state.sequence,
            "id": self.strategy_id,
            "strategy_id": self.strategy_id,
            "analysis_mode": "live_tick",
            "contract_version": 2,
            "status": status,
            "signal": signal,
            "direction": round(direction, 6),
            "score": round(direction, 6),
            "strength": round(strength, 6),
            "confidence": round(confidence, 6),
            "current_depth": round(depth, 6),
            "required_depth": round(self.required_depth, 6),
            "confidence_threshold": round(self.confidence_threshold, 6),
            "strength_threshold": round(self.strength_threshold, 6),
            "threshold": round(self.confidence_threshold, 6),
            "weight": round(self.weight, 6),
            "weight_applied": round(effective, 6),
            "ratio": round(self.weight, 6),
            "ready": ready,
            "analysis_state": contract_state,
            "state": contract_state,
            "context_factor": round(context_factor, 6),
            "quality": "good" if ready else "low",
            "warnings": [] if ready else ["STRATEGY_COMPONENT_NOT_READY"],
            "metadata": {
                "method": "descriptive_validated_tick",
                "depth_evidence": depth_parts,
                **(evidence or {}),
            },
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "invalid": self.invalid,
            "states": [
                {
                    "scope": list(scope),
                    "prices": list(s.prices),
                    "bids": list(s.bids),
                    "asks": list(s.asks),
                    "timestamps": list(s.timestamps),
                    "returns": list(s.returns),
                    "sequence": s.sequence,
                }
                for scope, s in self.states.items()
            ],
        }

    def restore(self, payload: Any) -> None:
        self.states = {}
        if (
            not isinstance(payload, dict)
            or payload.get("strategy_id") != self.strategy_id
        ):
            return
        self.invalid = max(0, int(payload.get("invalid", 0)))
        for row in payload.get("states") or []:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("scope"), list)
                or len(row["scope"]) != 3
            ):
                continue
            try:
                state = StrategicTickState()
                state.prices.extend(float(x) for x in row.get("prices", []))
                state.bids.extend(float(x) for x in row.get("bids", []))
                state.asks.extend(float(x) for x in row.get("asks", []))
                state.timestamps.extend(float(x) for x in row.get("timestamps", []))
                state.returns.extend(float(x) for x in row.get("returns", []))
                state.sequence = max(0, int(row.get("sequence", 0)))
                self.states[tuple(str(x) for x in row["scope"])] = state
            except (TypeError, ValueError):
                continue
