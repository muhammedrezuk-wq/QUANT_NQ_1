from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomContext
from shared.learning_model import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, schema_hash, train_softmax

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



class Logger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, list[Any]] = {}

    def subscribe(self, name: str, handler: Any) -> None:
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, payload))

    def payloads(self, name: str) -> list[dict[str, Any]]:
        return [payload for event, payload in self.events if event == name]


def manifest_config(atom_id: int) -> dict[str, Any]:
    import yaml
    folder = next((ATOM_ROOT).glob(f"{atom_id}_*"))
    data = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
    return dict(data.get("config") or {})


def validated_tick(index: int, *, price: float, symbol: str = "NQ") -> dict[str, Any]:
    return {"account_id": "A", "broker": "Raw Trading Ltd", "provider": "CTRADER",
            "symbol": symbol, "bid": price - 0.01, "ask": price + 0.01,
            "price": price, "volume": 10 + index,
            "timestamp": 1_800_000_000.0 + index / 1000.0,
            "exchange_timestamp": 1_800_000_000.0 + index / 1000.0}


def load_atom(atom_id: int):
    folder = next((ATOM_ROOT).glob(f"{atom_id}_*"))
    name = f"learning_atom_{atom_id}_{id(folder)}"
    spec = importlib.util.spec_from_file_location(name, folder / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def make_atom(atom_id: int, config: dict[str, Any] | None = None):
    module = load_atom(atom_id)
    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id, config or {}, Logger(), bus.publish, bus.subscribe))
    await atom.start()
    return module, atom, bus


def feature(sample_id: str, label: str, offset: float = 0.0) -> dict[str, Any]:
    vector = [offset + (i + 1) / 100.0 for i in range(len(FEATURE_NAMES))]
    return {"sample_id": sample_id, "training_eligible": True,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_schema_hash": schema_hash(),
            "feature_names": list(FEATURE_NAMES), "feature_vector": vector,
            "label": label}


def artifact() -> dict[str, Any]:
    rows = [[(i + j) / 10.0 for j in range(len(FEATURE_NAMES))]
            for i in range(1, 7)]
    labels = ["buy", "buy", "sell", "sell", "neutral", "neutral"]
    model = train_softmax(rows, labels, epochs=5, learning_rate=0.05, l2=0.001)
    model.update({"model_version": "test-v1", "accuracy": 0.8,
                  "balanced_accuracy": 0.8, "log_loss": 0.8,
                  "passed": True})
    return model
