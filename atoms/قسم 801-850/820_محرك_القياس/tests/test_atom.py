import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom820", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom820"] = _mod
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
async def test_three_levels_and_unknown_utility():
    a = Atom()
    ctx = _Ctx({"window": 10, "stale_after_s": 60, "warming_min": 2})
    await a.initialize(AtomContext(820, ctx.config, _Log(), ctx.publish, ctx.subscribe))
    await a.start()
    await a._on_tick({"account_id": "A", "broker": "BR", "symbol": "NQ", "timestamp": 5.0})
    handler = dict(ctx.handlers)["analysis.section.live"]
    await handler({"account_id": "A", "broker": "BR", "symbol": "NQ",
                   "timestamp": 5.1, "unified": {"state": "READY"}})
    by_event = dict(ctx.events)
    body = by_event["measurement.health.state"]
    assert body["trading_utility"] == "UNKNOWN"
    assert body["state"] in ("WARMING", "HEALTHY")
