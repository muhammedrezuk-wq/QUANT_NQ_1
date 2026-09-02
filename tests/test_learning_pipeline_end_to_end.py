from __future__ import annotations

from typing import Any
import pytest

from core.contracts.atom import AtomContext
from shared.learning_model import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, schema_hash
from tests.learning_test_support import Logger, load_atom


class DispatchBus:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []

    def subscribe(self, name: str, handler: Any) -> None:
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, payload))
        for handler in list(self.handlers.get(name, [])):
            await handler(dict(payload))
        if name == "model.persist_requested":
            await self.publish("model.persisted", {
                "model_name": payload.get("model_name"),
                "version": payload.get("version"), "persisted": True})

    def payloads(self, name: str) -> list[dict[str, Any]]:
        return [payload for event, payload in self.events if event == name]


@pytest.mark.asyncio
async def test_outcome_to_trained_shadow_model_survives_event_ordering():
    bus = DispatchBus()
    configs = {
        363: {"min_samples": 3, "validation_size": 1,
              "train_every_new_samples": 1, "epochs": 20,
              "learning_rate": 0.05, "l2": 0.001},
        364: {"validation_size": 1, "min_accuracy": 0.0},
        365: {"min_improvement": 0.0},
        367: {"mode": "shadow", "rollback_cooldown_seconds": 0},
        368: {"min_samples": 2, "min_accuracy": 0.4, "window_size": 3,
              "rollback_cooldown_seconds": 0},
    }
    atoms = []
    for atom_id in range(361, 369):
        module = load_atom(atom_id)
        atom = module.Atom()
        await atom.initialize(AtomContext(atom_id, configs.get(atom_id, {}),
                                          Logger(), bus.publish, bus.subscribe))
        atoms.append(atom)
    for atom in atoms:
        await atom.start()

    for index in range(4):
        vector = [index / 100 + j / 1000 for j in range(len(FEATURE_NAMES))]
        await bus.publish("learning.outcome.recorded", {
            "decision_id": f"d{index}", "outcome_event_id": f"o{index}",
            "account_id": "A", "broker": "B", "symbol": "NQ",
            "direction": "buy", "outcome": "buy", "realized_pnl": 1,
            "training_eligible": True,
            "model_evidence": {
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "feature_schema_hash": schema_hash(),
                "feature_names": list(FEATURE_NAMES),
                "feature_vector": vector,
            },
        })

    assert bus.payloads("learning.model.candidate")
    assert bus.payloads("learning.model.validated")[-1]["passed"] is True
    assert bus.payloads("learning.model.selected")[-1]["activation_stage"] == "shadow"
    assert bus.payloads("learning.model.active.state")[-1]["mode"] == "shadow"

    await bus.publish("market.tick.validated", {
        "account_id": "A", "broker": "B", "provider": "CTRADER",
        "symbol": "NQ", "bid": 100.99, "ask": 101.01, "price": 101,
        "volume": 20, "timestamp": 1_800_000_001.0,
        "exchange_timestamp": 1_800_000_001.0})
    evidence = bus.payloads("learning.model.evidence")[-1]
    assert evidence["state"] == "SHADOW"
    assert evidence["influence_weight"] == 0.0
