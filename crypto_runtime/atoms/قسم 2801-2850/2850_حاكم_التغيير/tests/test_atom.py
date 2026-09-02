import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom850", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom850"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

import pytest  # noqa: E402


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


class _Ctx:
    def __init__(self, cfg, bus=None):
        self.config = cfg
        self.handlers = []
        self.events = []
    def subscribe(self, event, handler):
        self.handlers.append((event, handler))
    async def publish(self, event, payload):
        self.events.append((event, payload))

@pytest.mark.asyncio
async def test_gates_reject_then_shadow():
    a = Atom()
    ctx = _Ctx({"min_evidence_windows": 1, "max_change_per_step": 0.25,
                "max_change_per_day": 0.5, "max_active_experiments": 2,
                "min_dwell_s": 0, "cooldown_s": 0, "max_changes_per_window": 5})
    await a.initialize(AtomContext(850, ctx.config, _Log(), ctx.publish, ctx.subscribe))
    await a.start()
    handler = dict(ctx.handlers)["recalibration.proposed"]
    await handler({"proposal_id": "p1", "target": "150",
                   "overall_drift": 0.8, "evidence": {"latency": 0.8}})
    by_event = dict(ctx.events)
    assert by_event["experiment.state"]["status"] == "APPROVED_FOR_SHADOW"
    assert by_event["experiment.state"]["max_authority"] == "APPROVED_FOR_SHADOW"
