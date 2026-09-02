from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from core.contracts.atom import AtomContext

_ATOM_PATH = Path(__file__).resolve().parents[1] / "atom.py"
_SPEC = importlib.util.spec_from_file_location("atom_870_tests", _ATOM_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
Atom = _MODULE.Atom


class Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.handlers: dict[str, list] = {}

    def subscribe(self, name, handler) -> None:
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload) -> None:
        self.events.append((name, payload))

    def events_named(self, name: str) -> list[dict]:
        return [payload for event, payload in self.events if event == name]


async def make_atom(tmp_path: Path) -> tuple[Atom, Bus]:
    bus = Bus()
    atom = Atom()
    config = {
        "db_path": str(tmp_path / "870.db"),
        "eval_window_s": 5,
        "verify_window_s": 5,
    }
    await atom.initialize(AtomContext(870, config, Logger(), bus.publish, bus.subscribe))
    await atom.start()
    return atom, bus


@pytest.mark.asyncio
async def test_calibration_brain_opens_shadow_experiment_and_applies(tmp_path: Path) -> None:
    atom, bus = await make_atom(tmp_path)
    await atom._on_kill({"adaptation_off": False, "active": True})
    await atom._on_drift({
        "section": "analysis",
        "overall_drift": 0.9,
        "threshold": 0.5,
        "baseline": {"version": "old"},
    })
    await atom._on_experiment({
        "status": "APPROVED_FOR_SHADOW",
        "experiment_id": "exp-1",
        "target": "analysis",
        "reason": "persistent drift",
    })
    for _ in range(5):
        await atom._on_second({})

    applied = bus.events_named("recalibration.applied")
    assert len(applied) == 1
    assert applied[0]["experiment_id"] == "exp-1"
    assert applied[0]["target"] == "analysis"
    assert applied[0]["old_baseline"] == {"version": "old"}

    state = await atom.snapshot()
    assert state["applied"] == 1
    assert state["open"]


@pytest.mark.asyncio
async def test_invalid_restore_does_not_create_calibration_action(tmp_path: Path) -> None:
    atom, bus = await make_atom(tmp_path)
    await atom.restore({"invalid": True})
    await atom._on_second({})
    assert bus.events_named("recalibration.applied") == []


@pytest.mark.asyncio
async def test_experiment_inputs_never_disappear_silently(tmp_path: Path) -> None:
    atom, bus = await make_atom(tmp_path)
    await atom._on_experiment({"status": "PENDING"})
    await atom._on_experiment({"status": "APPROVED_FOR_SHADOW", "target": "analysis"})

    health = await atom.health_check()
    assert health.details["ignored_status"] == 1
    assert health.details["invalid"] == 1
    state = await atom.snapshot()
    assert state["ignored_status"] == 1
    assert state["invalid"] == 1
    assert bus.events_named("recalibration.applied") == []
