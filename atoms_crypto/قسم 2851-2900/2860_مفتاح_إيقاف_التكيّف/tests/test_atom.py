import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom860", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom860"] = _mod
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
async def test_owner_command_only_and_trading_continues():
    a = Atom()
    ctx = _Ctx({"critical_drift": 1.0, "latency_budget_ms": 50})
    await a.initialize(AtomContext(860, ctx.config, _Log(), ctx.publish, ctx.subscribe))
    await a.start()
    handlers = dict(ctx.handlers)
    await handlers["drift.vector.state"]({"section": "150", "overall_drift": 2.0})
    assert a._adaptation_off and a._trips == 1
    assert ctx.last[1]["trading"] == "CONTINUES"
    await handlers["adaptation.kill_switch.command"]({"action": "ON"})     # بلا هوية
    assert a._adaptation_off                                              # رُفض
    await handlers["adaptation.kill_switch.command"]({"owner": "o", "action": "ON"})
    assert not a._adaptation_off


@pytest.mark.asyncio
async def test_latency_trip_scopes_to_tick_native_sections():
    """حكم المالك ٢٠٢٦-٠٨-٢٣: قسم البنية (200) شمعة-born — خارج القياس الساخن
    بالتصميم؛ والقسم التِكّيّ يُسقط عند التجاوز."""
    a = Atom()
    ctx = _Ctx({"critical_drift": 1.0, "latency_budget_ms": 50.0,
                "hot_path_sections": ["150", "350", "400"]})
    await a.initialize(AtomContext(860, ctx.config, _Log(), ctx.publish, ctx.subscribe))
    await a.start()
    handlers = dict(ctx.handlers)
    await handlers["measurement.latency.state"]({"section": "200", "p99_ms": 500.0})
    assert not a._adaptation_off, "قسم شمعة-born أسقط المفتاح — المسطرة الخطأ"
    assert a._latency_out_of_scope == 1
    await handlers["measurement.latency.state"]({"section": "150", "p99_ms": 500.0})
    assert a._adaptation_off and "150" in a._reason
