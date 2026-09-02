from __future__ import annotations

import importlib.util
import sys

import pytest

from core.contracts.atom import AtomContext, HealthState

_ATOM_PATH = __import__("pathlib").Path(__file__).resolve().parents[1] / "atom.py"
_SPEC = importlib.util.spec_from_file_location("atom_832_tests", _ATOM_PATH)
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

    def outputs(self) -> list[dict]:
        return [payload for name, payload in self.events if name == "system.diagnosis.state"]


async def make_atom() -> tuple[Atom, Bus]:
    bus = Bus()
    atom = Atom()
    await atom.initialize(AtomContext(832, {}, Logger(), bus.publish, bus.subscribe))
    await atom.start()
    return atom, bus


@pytest.mark.asyncio
async def test_diagnostician_emits_explained_degraded_state() -> None:
    atom, bus = await make_atom()
    await atom._on_alerts({"alerts": {"feed.timeout": {"severity": "HIGH"}}})
    await atom._on_section({"section": "analysis", "technical_health": "DEGRADED"})
    await atom._on_regime({"previous_regime": "TREND", "regime": "RANGE"})
    await atom._on_drift({"section": "analysis", "overall_drift": 0.8, "threshold": 0.5})
    await atom._on_second({})
    await atom._on_second({})
    await atom._on_second({})
    await atom._on_second({})
    await atom._on_second({})

    assert len(bus.outputs()) == 1
    result = bus.outputs()[0]
    assert result["state"] == "DEGRADED"
    assert result["primary_cause"] in {"DATA", "SYSTEM"}
    assert result["because"]
    assert result["facts"]


@pytest.mark.asyncio
async def test_diagnostician_missing_evidence_is_not_healthy() -> None:
    atom, bus = await make_atom()
    await atom._on_regime({"regime": "UNKNOWN"})
    await atom._on_second({})
    await atom._on_second({})
    await atom._on_second({})
    await atom._on_second({})
    await atom._on_second({})

    result = bus.outputs()[0]
    assert result["state"] in {"UNKNOWN", "OBSERVING"}
    health = await atom.health_check()
    assert health.state == HealthState.DEGRADED
