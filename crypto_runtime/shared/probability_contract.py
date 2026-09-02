"""Live tick contract for probability models 351-359.

The models consume only the canonical validated tick path (622 -> 613 -> 112).
No OHLC/candle field is used.  Every card carries measured depth, thresholds,
confidence, strength and an explicit weight gate, matching the live contract
principles used by section 150 without sharing its analyzer identities.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from shared.tick_contract import VALIDATED_TICK_EVENT, as_validated_tick

STATE_ANALYZING = "ANALYZING"
STATE_NOT_READY = "NOT_READY"
STATE_READY = "DECISION_READY"
STATE_STALE = "STALE"
STATE_INVALID = "INVALID"

BASE_MODEL_IDS = (
    "trend_model",
    "reversal_model",
    "breakout_model",
    "pullback_model",
    "momentum_model",
    "range_model",
    "hurst",
)
EQUAL_MODEL_WEIGHT = 100.0 / len(BASE_MODEL_IDS)


def finite(value: Any, fallback: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def clip(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    number = finite(value, 0.0) or 0.0
    return max(low, min(high, number))


@dataclass
class TickState:
    prices: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    bids: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    asks: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    volumes: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    returns: deque[float] = field(default_factory=lambda: deque(maxlen=255))
    sequence: int = 0


class TickModelRuntime:
    def __init__(self, model_id: str) -> None:
        if model_id not in BASE_MODEL_IDS:
            raise ValueError("UNKNOWN_PROBABILITY_MODEL")
        self.model_id = model_id
        self.states: dict[tuple[str, str, str], TickState] = {}
        self.weight = EQUAL_MODEL_WEIGHT
        self.required_depth = 60.0
        self.confidence_threshold = 60.0
        self.strength_threshold = 0.0
        self.required_ticks = 24
        self.invalid = 0

    def configure(self, config: dict[str, Any]) -> None:
        self.weight = clip(config.get("weight", EQUAL_MODEL_WEIGHT))
        self.required_depth = clip(config.get("required_depth", 60.0))
        self.confidence_threshold = clip(config.get("confidence_threshold", 60.0))
        self.strength_threshold = clip(config.get("strength_threshold", 0.0))
        self.required_ticks = max(2, int(config.get("required_ticks", 24)))

    @staticmethod
    def _scope(payload: dict[str, Any]) -> tuple[str, str, str] | None:
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        symbol = str(payload.get("symbol") or "").strip().upper()
        return (account, broker, symbol) if account and broker and symbol else None

    def ingest(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], TickState] | None:
        tick = as_validated_tick(payload)
        scope = self._scope(tick)
        price = finite(tick.get("price"))
        bid = finite(tick.get("bid"), price)
        ask = finite(tick.get("ask"), price)
        stamp = finite(tick.get("source_timestamp", tick.get("timestamp")))
        if (
            scope is None
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
        state = self.states.setdefault(scope, TickState())
        if state.timestamps and stamp <= state.timestamps[-1]:
            self.invalid += 1
            return None
        if state.prices:
            state.returns.append(price / state.prices[-1] - 1.0)
        state.prices.append(price)
        state.bids.append(bid)
        state.asks.append(ask)
        state.volumes.append(max(0.0, finite(tick.get("volume"), 0.0) or 0.0))
        state.timestamps.append(stamp)
        state.sequence += 1
        return tick, state

    def depth(self, state: TickState) -> tuple[float, dict[str, float]]:
        returns = list(state.returns)[-self.required_ticks :]
        sample = clip(len(returns) / self.required_ticks * 100.0)
        movement = clip(sum(abs(value) for value in returns) * 160_000.0)
        spreads = [
            (ask - bid) / price
            for bid, ask, price in zip(
                list(state.bids)[-self.required_ticks :],
                list(state.asks)[-self.required_ticks :],
                list(state.prices)[-self.required_ticks :],
            )
            if price > 0
        ]
        spread_quality = clip(
            100.0 - (sum(spreads) / len(spreads) if spreads else 0.0) * 200_000.0
        )
        stamps = list(state.timestamps)[-self.required_ticks :]
        if len(stamps) > 2:
            gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
            mean_gap = sum(gaps) / len(gaps) if gaps else 0.0
            continuity = clip(
                100.0 - max(gaps, default=0.0) / max(mean_gap, 0.001) * 15.0
            )
        else:
            continuity = 0.0
        current = (
            0.40 * sample + 0.30 * movement + 0.15 * spread_quality + 0.15 * continuity
        )
        return clip(current), {
            "sample": sample,
            "movement": movement,
            "spread_quality": spread_quality,
            "continuity": continuity,
        }

    def card(
        self,
        tick: dict[str, Any],
        state: TickState,
        *,
        direction: float,
        strength: float,
        confidence: float,
        probability: float,
        signal: str,
        status: str = "ok",
        evidence: dict[str, Any] | None = None,
        directional: bool = True,
    ) -> dict[str, Any]:
        current_depth, depth_evidence = self.depth(state)
        direction = max(-100.0, min(100.0, float(direction)))
        strength = clip(strength)
        confidence = clip(confidence)
        probability = max(0.0, min(1.0, float(probability)))
        ready = (
            status == "ok"
            and current_depth >= self.required_depth
            and confidence >= self.confidence_threshold
            and strength >= self.strength_threshold
            and (not directional or direction != 0.0)
        )
        state_name = STATE_READY if ready else STATE_NOT_READY
        weight_applied = self.weight if ready else 0.0
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
            "id": self.model_id,
            "model_id": self.model_id,
            "analysis_mode": "live_tick",
            "contract_version": 2,
            "status": status,
            "signal": signal,
            "direction": round(direction, 6),
            "strength": round(strength, 6),
            "confidence": round(confidence, 6),
            "probability": round(probability, 8),
            "score": round(direction, 6),
            "current_depth": round(current_depth, 6),
            "required_depth": round(self.required_depth, 6),
            "confidence_threshold": round(self.confidence_threshold, 6),
            "strength_threshold": round(self.strength_threshold, 6),
            "threshold": round(self.confidence_threshold, 6),
            "weight": round(self.weight, 6),
            "weight_applied": round(weight_applied, 6),
            "ratio": round(self.weight, 6),
            "ready": ready,
            "analysis_state": state_name,
            "state": state_name,
            "quality": "good" if ready else "low",
            "warnings": [] if ready else ["MODEL_NOT_READY"],
            "metadata": {
                "method": "validated_tick",
                "probability": round(probability, 8),
                "depth_evidence": depth_evidence,
                **(evidence or {}),
            },
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "invalid": self.invalid,
            "states": [
                {
                    "scope": list(scope),
                    "prices": list(state.prices),
                    "bids": list(state.bids),
                    "asks": list(state.asks),
                    "volumes": list(state.volumes),
                    "timestamps": list(state.timestamps),
                    "returns": list(state.returns),
                    "sequence": state.sequence,
                }
                for scope, state in self.states.items()
            ],
        }

    def restore(self, payload: Any) -> None:
        self.states = {}
        if not isinstance(payload, dict) or payload.get("model_id") != self.model_id:
            return
        self.invalid = max(0, int(payload.get("invalid", 0)))
        for row in payload.get("states") or []:
            if not isinstance(row, dict) or not isinstance(row.get("scope"), list):
                continue
            scope_values = row["scope"]
            if len(scope_values) != 3 or not all(
                isinstance(x, str) for x in scope_values
            ):
                continue
            state = TickState()
            try:
                state.prices.extend(float(x) for x in row.get("prices", []))
                state.bids.extend(float(x) for x in row.get("bids", []))
                state.asks.extend(float(x) for x in row.get("asks", []))
                state.volumes.extend(float(x) for x in row.get("volumes", []))
                state.timestamps.extend(float(x) for x in row.get("timestamps", []))
                state.returns.extend(float(x) for x in row.get("returns", []))
                state.sequence = max(0, int(row.get("sequence", 0)))
            except (TypeError, ValueError):
                continue
            self.states[tuple(scope_values)] = state


def common_config_schema() -> dict[str, Any]:
    return {
        "weight": {"type": "number", "minimum": 0, "maximum": 100},
        "required_depth": {"type": "number", "minimum": 0, "maximum": 100},
        "confidence_threshold": {"type": "number", "minimum": 0, "maximum": 100},
        "strength_threshold": {"type": "number", "minimum": 0, "maximum": 100},
        "required_ticks": {"type": "integer", "minimum": 2, "maximum": 10000},
    }
