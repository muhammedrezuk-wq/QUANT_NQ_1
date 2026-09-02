import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom840", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom840"] = _mod
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
async def test_drift_against_baseline_proposes_only():
    a = Atom()
    ctx = _Ctx({"warmup_windows": 2, "drift_threshold": 0.5})
    await a.initialize(AtomContext(840, ctx.config, _Log(), ctx.publish, ctx.subscribe))
    await a.start()
    handler = dict(ctx.handlers)["measurement.health.state"]
    for ratio in (0.95, 0.95):             # خط الأساس
        await handler({"section": "150", "ready_ratio": ratio})
    for ratio in (0.10, 0.10):             # تدهور
        await handler({"section": "150", "ready_ratio": ratio})
    proposals = [p for e, p in ctx.events if e == "recalibration.proposed"]
    assert proposals and proposals[-1]["reason"] == "DRIFT_THRESHOLD_EXCEEDED"


@pytest.mark.asyncio
async def test_kill_switch_suppression_and_cooldown():
    """قياس المالك: المطفي يكفّ عن الاقتراح، والقسم لا يُقترح كل نافذة."""
    a = Atom()
    ctx = _Ctx({"warmup_windows": 2, "drift_threshold": 0.5,
                "proposal_cooldown_windows": 3})
    await a.initialize(AtomContext(840, ctx.config, _Log(), ctx.publish, ctx.subscribe))
    await a.start()
    handlers = dict(ctx.handlers)
    for ratio in (0.95, 0.95):
        await handlers["measurement.health.state"]({"section": "150", "ready_ratio": ratio})
    await handlers["measurement.health.state"]({"section": "150", "ready_ratio": 0.10})
    # العبور الأول: اقتراح واحد ثم تهدئة
    proposals = [p for e, p in ctx.events if e == "recalibration.proposed"]
    assert len(proposals) == 1
    # التهدئة 3 نوافذ: ثلاثة صفوف متدهرة إضافية داخل التهدئة → لا اقتراح جديد
    for _ in range(3):
        await handlers["measurement.health.state"]({"section": "150", "ready_ratio": 0.10})
    proposals = [p for e, p in ctx.events if e == "recalibration.proposed"]
    assert len(proposals) == 1, "التهدئة لم تمنع تكرار الاقتراح"
    # والنافذة الرابعة بعد انقضاء التهدئة: يحقّ له الاقتراح من جديد
    await handlers["measurement.health.state"]({"section": "150", "ready_ratio": 0.10})
    proposals = [p for e, p in ctx.events if e == "recalibration.proposed"]
    assert len(proposals) == 2, "التهدئة لا تنقضي"
    # المفتاح مطفأ → حتى بعد انقضاء التهدئة: كبت معلَن
    a._cooldowns["150"] = 0  # انقضت التهدئة — البوابة الوحيدة المتبقية هي المفتاح
    await handlers["adaptation.kill_switch.state"]({"adaptation_off": True})
    await handlers["measurement.health.state"]({"section": "150", "ready_ratio": 0.05})
    proposals = [p for e, p in ctx.events if e == "recalibration.proposed"]
    assert len(proposals) == 2 and a._suppressed >= 1, "المطفي لم يكبت الاقتراح"
    health = await a.health_check()
    assert health.details["adaptation_off"] is True and health.details["suppressed"] >= 1
