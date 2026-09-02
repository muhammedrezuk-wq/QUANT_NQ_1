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
    "_atom165", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom165"] = _mod
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
        return AtomContext(atom_id=165, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"window": 4, "noisy_max": 0.3, "efficient_min": 0.6}


def _candle(close, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "close": close, "timeframe": tf, "period_start": "1"}


async def _run(closes):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    for c in closes:
        await atom._on_candle(_candle(c))
    return [p for n, p in bus.published if n == EVENT_OUT]


def _oks(refs):
    return [r for r in refs if r["status"] == "ok"]


async def test_efficient():
    print("\n--- test_efficient ---")
    o = _oks(await _run([100, 101, 102, 103, 104]))[-1]  # straight trend, ER=1
    assert o["signal"] == "efficient", o["signal"]
    print(f"OK — كفاءة: ER={o['metadata']['efficiency_ratio']}")


async def test_noisy():
    print("\n--- test_noisy ---")
    o = _oks(await _run([100, 102, 100, 102, 100]))[-1]  # chop, net 0 -> ER 0
    assert o["signal"] == "noisy", o["signal"]
    print(f"OK — ضوضاء: ER={o['metadata']['efficiency_ratio']}")


async def test_normal():
    print("\n--- test_normal ---")
    o = _oks(await _run([100, 101, 100, 101, 102]))[-1]  # net 2 / path 4 = 0.5
    assert o["signal"] == "normal", o["signal"]
    print(f"OK — عادي: ER={o['metadata']['efficiency_ratio']}")


async def test_warmup_insufficient():
    print("\n--- test_warmup_insufficient ---")
    refs = await _run([100, 101, 102])  # below window+1
    assert refs and all(r["status"] == "insufficient_data" for r in refs)
    print("OK — إحماء: insufficient_data")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    o = _oks(await _run([100, 101, 102, 103, 104]))[-1]
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in o, f"حقل ناقص: {f}"
    for f in ("method", "efficiency_ratio", "net_move", "total_path"):
        assert f in o["metadata"], f"metadata ناقص: {f}"
    assert o["id"] == "noise"
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
    for c in [100, 101, 102, 103, 104]:
        await atom._on_candle(_candle(c))
    key = ("NQ100", "60s")
    before = list(atom._state[key])
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "سلسلة الإغلاقات لازم تُحفظ بجانبه — لا تُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    after = list(revived._state[key])
    assert after == before, (after, before)
    assert revived._candles_seen == atom._candles_seen

    await revived._on_candle(_candle(105))
    fresh = [p for n, p in bus2.published if n == EVENT_OUT]
    assert fresh and fresh[-1]["status"] == "ok", fresh[-1]["status"]
    print(f"OK — سلسلة الإغلاقات نجت الإقلاع: closes={len(after)} بلا إحماء جديد")


async def main():
    tests = [test_efficient, test_noisy, test_normal, test_warmup_insufficient,
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
