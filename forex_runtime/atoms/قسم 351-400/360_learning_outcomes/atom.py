from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.1"
SNAPSHOT_VERSION = 1
LABEL_POLICY_VERSION = "executed_direction_net_pnl_v2"

EVENT_SCORED = "decision.scored.state"
EVENT_DECISION = "decision.resolved.state"
EVENT_FINAL = "trading.final_decision"
EVENT_OUTCOME = "market.outcome.realized"
EVENT_TICK = "market.tick.validated"
EVENT_OUT = "learning.outcome.recorded"

SEP = "\x1f"
_SOURCE_RANK = {
    EVENT_SCORED: 1,
    EVENT_DECISION: 2,
    EVENT_FINAL: 3,
}


def num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def key(account_id: Any, symbol: Any) -> str:
    return str(account_id or "") + SEP + str(symbol or "")


def _text(value: Any) -> str:
    return str(value or "").strip()


@section_atom("350", "360")
class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._decisions: dict[str, list[dict[str, Any]]] = {}
        self._prices: dict[str, float] = {}
        self._records = 0
        self._decisions_seen = 0
        self._scored_seen = 0
        self._resolved_seen = 0
        self._final_seen = 0
        self._unmatched = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_SCORED, self._on_scored)
        context.subscribe(EVENT_DECISION, self._on_decision)
        context.subscribe(EVENT_FINAL, self._on_final)
        context.subscribe(EVENT_OUTCOME, self._on_outcome)
        context.subscribe(EVENT_TICK, self._on_tick)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict) or not payload.get("symbol"):
            return
        bid = num(payload.get("bid"))
        ask = num(payload.get("ask"))
        price = num(payload.get("price"))
        if price is None and bid is not None and ask is not None:
            price = (bid + ask) / 2.0
        if price is not None:
            self._prices[str(payload["symbol"])] = price

    def _decision_record(self, payload: dict[str, Any], source: str) -> dict[str, Any]:
        symbol = _text(payload.get("symbol"))
        account_id = _text(payload.get("account_id"))
        decision_id = _text(
            payload.get("decision_id")
            or payload.get("request_id")
            or payload.get("cycle_id")
            or payload.get("event_id")
        )
        model_evidence = payload.get("model_evidence") if isinstance(payload.get("model_evidence"), dict) else {}
        return {
            "decision_id": decision_id,
            "account_id": account_id,
            "symbol": symbol,
            "timestamp": payload.get("timestamp"),
            "model_version": payload.get("model_version") or model_evidence.get("model_version"),
            "model_direction": model_evidence.get("direction"),
            # Preserve the exact evidence that existed at decision time.  The
            # learner must never rebuild old features from newer market data.
            "features": dict(payload.get("features")) if isinstance(payload.get("features"), dict) else {},
            "feature_snapshot": dict(payload.get("feature_snapshot")) if isinstance(payload.get("feature_snapshot"), dict) else {},
            "model_evidence": dict(model_evidence),
            "strategy_evidence": dict(payload.get("evidence")) if isinstance(payload.get("evidence"), dict) else (
                dict(payload.get("metadata")) if isinstance(payload.get("metadata"), dict) else {}),
            "direction": _text(payload.get("direction") or payload.get("signal") or "neutral").lower(),
            "confidence": num(payload.get("confidence")) or num(payload.get("strength")) or 0.0,
            "score": num(payload.get("score")) or 0.0,
            "price_at_decision": num(payload.get("price_at_decision")) or self._prices.get(symbol),
            "target_net": payload.get("target_net"),
            "request_id": payload.get("request_id"),
            "cycle_id": payload.get("cycle_id"),
            "source_event": source,
            "source_rank": _SOURCE_RANK.get(source, 0),
            "decision_missing": False,
            "training_eligible": True,
        }

    async def _capture_decision(self, payload: dict[str, Any], source: str) -> None:
        if not self._running or not isinstance(payload, dict) or not payload.get("symbol"):
            return
        record = self._decision_record(payload, source)
        bucket = self._decisions.setdefault(key(record["account_id"], record["symbol"]), [])
        identity = record["decision_id"]
        if identity:
            for existing in bucket:
                if existing.get("decision_id") == identity:
                    if record["source_rank"] >= int(existing.get("source_rank") or 0):
                        existing.update({k: v for k, v in record.items() if v not in (None, "")})
                    return
        bucket.append(record)
        self._decisions_seen += 1

    async def _on_scored(self, payload: dict[str, Any]) -> None:
        self._scored_seen += 1
        await self._capture_decision(payload, EVENT_SCORED)

    async def _on_decision(self, payload: dict[str, Any]) -> None:
        self._resolved_seen += 1
        await self._capture_decision(payload, EVENT_DECISION)

    async def _on_final(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict) or not payload.get("symbol"):
            return
        self._final_seen += 1
        bucket = self._decisions.setdefault(key(payload.get("account_id"), payload.get("symbol")), [])
        request_id = _text(payload.get("request_id"))
        cycle_id = _text(payload.get("cycle_id"))
        match = next(
            (
                item for item in reversed(bucket)
                if (request_id and _text(item.get("request_id")) == request_id)
                or (cycle_id and _text(item.get("cycle_id")) == cycle_id)
            ),
            None,
        )
        if match is None:
            await self._capture_decision(payload, EVENT_FINAL)
            return
        match.update({
            "request_id": payload.get("request_id") or match.get("request_id"),
            "cycle_id": payload.get("cycle_id") or match.get("cycle_id"),
            "target_net": payload.get("target_net", match.get("target_net")),
            "price_at_decision": num(payload.get("reference_price")) or match.get("price_at_decision"),
            "source_event": EVENT_FINAL,
            "source_rank": _SOURCE_RANK[EVENT_FINAL],
        })

    def _find_decision(self, payload: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
        bucket = self._decisions.get(key(payload.get("account_id"), payload.get("symbol")))
        if not bucket:
            return bucket, None
        identities = {
            _text(payload.get("decision_id")),
            _text(payload.get("request_id")),
            _text(payload.get("cycle_id")),
        }
        identities.discard("")
        if identities:
            for item in reversed(bucket):
                item_ids = {
                    _text(item.get("decision_id")),
                    _text(item.get("request_id")),
                    _text(item.get("cycle_id")),
                }
                if identities & item_ids:
                    return bucket, item
        return bucket, bucket[0]

    async def _on_outcome(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict) or not payload.get("symbol"):
            return
        bucket, decision = self._find_decision(payload)
        if decision is not None and bucket is not None:
            bucket.remove(decision)
            if not bucket:
                self._decisions.pop(key(payload.get("account_id"), payload.get("symbol")), None)
        else:
            self._unmatched += 1
            outcome_id = _text(payload.get("event_id") or payload.get("ticket") or f"{self._records + 1}")
            side = _text(payload.get("side")).lower()
            fallback_direction = side if side in ("buy", "sell") else "neutral"
            decision = {
                "decision_id": f"unmatched:{outcome_id}",
                "account_id": _text(payload.get("account_id")),
                "symbol": _text(payload.get("symbol")),
                "direction": fallback_direction,
                "confidence": 0.0,
                "score": 0.0,
                "features": {},
                "feature_snapshot": {},
                "model_evidence": {},
                "strategy_evidence": {},
                "model_version": None,
                "model_direction": None,
                "price_at_decision": None,
                "target_net": None,
                "decision_missing": True,
                "training_eligible": False,
                "missing_reason": "NO_MATCHING_DECISION",
                "source_event": "market.outcome.realized",
            }

        profit = num(payload.get("profit")) or 0.0
        direction = str(decision.get("direction") or "neutral").lower()
        if decision.get("decision_missing"):
            label = "neutral"
        else:
            label = direction if profit > 0 else (
                "sell" if direction == "buy" else "buy" if direction == "sell" else "neutral"
            ) if profit < 0 else "neutral"
        record = {
            **decision,
            "outcome": label,
            "label_policy_version": LABEL_POLICY_VERSION,
            "feature_cutoff_time": decision.get("timestamp"),
            "label_time": payload.get("timestamp"),
            "realized_pnl": profit,
            "actual_net": payload.get("actual_net"),
            "mfe": payload.get("mfe"),
            "mae": payload.get("mae"),
            "duration": payload.get("duration"),
            "exit_reason": payload.get("reason"),
            "ticket": payload.get("ticket"),
            "outcome_event_id": payload.get("event_id"),
        }
        self._records += 1
        await self._context.publish(EVENT_OUT, record)

    async def snapshot(self) -> dict[str, Any]:
        return {
            "snapshot_version": SNAPSHOT_VERSION,
            "decisions": self._decisions,
            "prices": self._prices,
            "records": self._records,
            "decisions_seen": self._decisions_seen,
            "scored_seen": self._scored_seen,
            "resolved_seen": self._resolved_seen,
            "final_seen": self._final_seen,
            "unmatched": self._unmatched,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or state.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ValueError("INVALID_LEARNING_OUTCOME_SNAPSHOT")
        decisions = state.get("decisions", {})
        prices = state.get("prices", {})
        if not isinstance(decisions, dict) or not isinstance(prices, dict):
            raise ValueError("INVALID_LEARNING_OUTCOME_SNAPSHOT")
        self._decisions = {str(k): [dict(v) for v in values if isinstance(v, dict)]
                           for k, values in decisions.items() if isinstance(values, list)}
        self._prices = {str(k): float(v) for k, v in prices.items()}
        self._records = max(0, int(state.get("records", 0)))
        self._decisions_seen = max(0, int(state.get("decisions_seen", 0)))
        self._scored_seen = max(0, int(state.get("scored_seen", 0)))
        self._resolved_seen = max(0, int(state.get("resolved_seen", 0)))
        self._final_seen = max(0, int(state.get("final_seen", 0)))
        self._unmatched = max(0, int(state.get("unmatched", 0)))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {
            "records": self._records,
            "decisions_seen": self._decisions_seen,
            "scored_seen": self._scored_seen,
            "resolved_seen": self._resolved_seen,
            "final_seen": self._final_seen,
            "unmatched_outcomes": self._unmatched,
            "pending": sum(len(items) for items in self._decisions.values()),
        }
        if self._records:
            message = "outcomes=%d unmatched=%d decisions=%d" % (
                self._records, self._unmatched, self._decisions_seen,
            )
            return HealthStatus(state=HealthState.HEALTHY, message=message, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="READY_AWAITING_FIRST_CLOSED_TRADE_OUTCOME | outcomes=0",
            details=details,
        )
