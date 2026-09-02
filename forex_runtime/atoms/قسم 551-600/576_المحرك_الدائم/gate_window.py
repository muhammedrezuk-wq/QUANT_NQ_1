from __future__ import annotations

from typing import Any

from shared.financial_scope import text

# Campaign 450-901 batch B (2026-08-23): the gate window lives in its own
# module -- same pattern as 578's seven helpers. A parent_decision_id is only
# accepted when gate 467 actually published decision.gate.passed for it.
GATE_WINDOW_MAX = 2048


class GateWindow:
    """Bounded FIFO of decisions that actually crossed the gate."""

    def __init__(self) -> None:
        self.decisions: dict[str, str] = {}
        self.rejected: int = 0

    def observe(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        decision_id = text(payload.get("decision_id"))
        if not decision_id:
            return
        self.decisions[decision_id] = text(payload.get("gate_request_id"))
        while len(self.decisions) > GATE_WINDOW_MAX:
            self.decisions.pop(next(iter(self.decisions)))

    def has(self, decision_id: str) -> bool:
        return decision_id in self.decisions

    def state(self) -> dict[str, str]:
        return dict(self.decisions)


async def publish_rejection(context, event_rejected: str, account_id: str, broker: str,
                            symbol: str, payload: dict, reason: str,
                            extra: dict | None = None) -> None:
    """One rejection publisher for both guards -- no duplicated shapes."""
    if context is None:
        return
    body = {"account_id": account_id, "broker": broker, "symbol": symbol,
            "status": "REJECTED", "reason": reason,
            "origin": text(payload.get("origin")),
            "operator": text(payload.get("operator"))}
    if extra:
        body.update(extra)
    await context.publish(event_rejected, body)
