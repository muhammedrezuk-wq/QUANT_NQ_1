# -*- coding: utf-8 -*-
"""v5.3.0: ذاكرة الأزواج تنجو من أي موت — الجذر المقاس 2026-08-25:
موت غير نظيف محا اللقطة الوحيدة، فصارت الساق الحية «غريبة» وتجمّد المسار
كله على SNAPSHOT_DISAGREES_WITH_BROKER."""
import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[3]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(folder))

spec = importlib.util.spec_from_file_location("_atom578_durable", folder / "atom.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["_atom578_durable"] = mod
spec.loader.exec_module(mod)


class _Log:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class _Bus:
    def __init__(self):
        self.events = []

    def subscribe(self, *a, **k): pass

    async def publish(self, name, payload):
        self.events.append((name, payload))


async def _boot(store):
    bus = _Bus()
    atom = mod.Atom()
    ctx = mod.AtomContext(578, {"lot_step": .01, "min_volume": .01, "reward_risk": 2,
                                "max_attempts": 3, "catastrophe_stop_multiple": 3.0,
                                "fallback_stop_frac": .02, "pair_store_path": store},
                          _Log(), bus.publish, bus.subscribe)
    await atom.initialize(ctx)
    await atom.start()
    return atom, bus


def test_pairs_survive_unclean_death():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = str(Path(tmp) / "pairs.db")
            # الحياة الأولى: زوج يسجَّل وساقه تصير فعلية بتذكرة حقيقية —
            # ثم «موت غير نظيف»: لا snapshot ولا إيقاف، الكائن يُرمى فقط.
            a1, _ = await _boot(store)
            await a1._on_external({"official_time": 1000.0})
            leg = {"pair_id": "P1", "leg_role": "BUY", "request_id": "P1-buy-a1",
                   "account_id": "A", "symbol": "X", "side": "BUY", "volume": 0.25}
            await a1._on_requested(leg)
            await a1._on_trade({"event_type": "OPENED", "request_id": "P1-buy-a1",
                               "ticket": 111, "account_id": "A", "symbol": "X"})
            assert a1._pairs["P1"]["legs"]["BUY"]["ticket"] == 111
            del a1  # موت غير نظيف — بلا snapshot
            # الحياة الثانية: الذاكرة تعود من المخزن الدائم وحده
            a2, _ = await _boot(store)
            assert a2._durable_pairs_loaded is True
            assert "P1" in a2._pairs, a2._pairs
            assert a2._pairs["P1"]["legs"]["BUY"]["ticket"] == 111
            assert a2._request_map.get("P1-buy-a1") == ("P1", "BUY")
            # التذكرة الحية عند الوسيط ما عادت «غريبة» — لا تجميد
            await a2._on_positions({"account_id": "A", "broker": "BR",
                                    "usable_for_new_exposure": True,
                                    "usable_for_protection": True,
                                    "positions": [{"ticket": 111, "symbol": "X",
                                                   "side": "BUY", "volume": 0.25}]})
            assert a2._reconciled is True, a2._reconcile_reason
            # ولقطة إيقاف نظيف قديمة (فارغة) لا تدوس الذاكرة الدائمة
            await a2.restore({"schema_version": 1, "written_at": 0.0,
                              "session_epoch": None,
                              "payload": {"version": "x", "counter": 0, "pairs": {},
                                          "flood_guard": {}},
                              "digest": "bogus"})
            assert "P1" in a2._pairs

    asyncio.run(scenario())
