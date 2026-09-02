import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom830", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom830"] = _mod
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
    def subscribe(self, event, handler):
        self.handlers.append((event, handler))
    async def publish(self, event, payload):
        self.last = (event, payload)

@pytest.mark.asyncio
async def test_hysteresis_requires_confirmation():
    a = Atom()
    ctx = _Ctx({"entry_threshold": 70, "exit_threshold": 45,
                "confirmation_window": 3, "min_duration_s": 0, "volatility_high": 75})
    await a.initialize(AtomContext(830, ctx.config, _Log(), ctx.publish, ctx.subscribe))
    await a.start()
    handler = dict(ctx.handlers)["structure.trend.state"]
    await handler({"symbol": "NQ", "direction": 90})
    await handler({"symbol": "NQ", "direction": 90})
    assert a._regime == "RANGING"          # تأكيدان لا يكفيان
    await handler({"symbol": "NQ", "direction": 90})
    assert a._regime == "TRENDING"         # الثالث يُكمل النافذة
    assert "OBSERVATION_ONLY" in ctx.last[1]["authority"]
