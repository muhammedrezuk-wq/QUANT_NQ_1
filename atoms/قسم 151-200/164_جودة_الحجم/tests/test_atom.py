import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom164", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom164"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT


class _NullLogger:
    def debug(self, *a): pass
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass
    def critical(self, *a): pass


class FakeEventBus:
    def __init__(self):
        self.published = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=164, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"baseline_window": 5, "low_ratio": 0.4}


def _candle(volume, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "close": 100.0, "volume": volume,
            "timeframe": tf, "period_start": "1"}


async def _run(volumes):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    for v in volumes:
        await atom._on_candle(_candle(v))
    return [p for n, p in bus.published if n == EVENT_OUT]


def _oks(refs):
    return [r for r in refs if r["status"] == "ok"]


_WARMUP = [100, 100, 100, 100, 100]  # baseline_window=5


async def test_ok():
    print("\n--- test_ok ---")
    o = _oks(await _run(_WARMUP + [100]))[-1]
    assert o["signal"] == "ok", o["signal"]
    assert o["metadata"]["source"] == "tick"
    print(f"OK — موثوق: ratio={o['metadata']['ratio']} source={o['metadata']['source']}")


async def test_low():
    print("\n--- test_low ---")
    o = _oks(await _run(_WARMUP + [20]))[-1]  # 20 vs baseline 100 = 0.2 < 0.4
    assert o["signal"] == "low", o["signal"]
    print(f"OK — شحيح: ratio={o['metadata']['ratio']}")


async def test_missing():
    print("\n--- test_missing ---")
    o = _oks(await _run(_WARMUP + [0]))[-1]  # zero volume
    assert o["signal"] == "missing", o["signal"]
    print("OK — مفقود: signal=missing (status=ok)")


async def test_missing_field():
    print("\n--- test_missing_field ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle({"symbol": "NQ100", "close": 100.0, "timeframe": "60s",
                           "period_start": "1"})  # no volume field
    o = [p for n, p in bus.published if n == EVENT_OUT][-1]
    assert o["signal"] == "missing", o["signal"]
    print("OK — حقل حجم غائب → missing")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    o = _oks(await _run(_WARMUP + [100]))[-1]
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in o, f"حقل ناقص: {f}"
    for f in ("method", "source", "volume", "baseline_volume", "ratio"):
        assert f in o["metadata"], f"metadata ناقص: {f}"
    assert o["id"] == "volume_quality"
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(100))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_state_survives_restart():
    print("\n--- test_state_survives_restart ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    for v in _WARMUP:
        await atom._on_candle(_candle(v))
    key = ("NQ100", "60s")
    before = list(atom._state[key]["vol"])
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "خطّ أساس الحجم لازم يُحفظ بجانبه — لا يُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    after = list(revived._state[key]["vol"])
    assert after == before, (after, before)
    assert revived._candles_seen == atom._candles_seen

    await revived._on_candle(_candle(100))
    fresh = [p for n, p in bus2.published if n == EVENT_OUT]
    assert fresh and fresh[-1]["status"] == "ok", fresh[-1]["status"]
    print(f"OK — خطّ أساس الحجم نجا الإقلاع: vol={len(after)} بلا إحماء جديد")


async def main():
    tests = [test_ok, test_low, test_missing, test_missing_field,
             test_contract_shape, test_health_states, test_state_survives_restart]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
