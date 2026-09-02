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
    "_atom316", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom316"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"window_size": 20, "min_quality": 0.95}


class _NullLogger:
    def debug(self, *a): pass
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass
    def critical(self, *a): pass


class FakeEventBus:
    def __init__(self):
        self.published = []
        self._handlers = {}

    def subscribe(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=316, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tick(price, sequence, symbol="NQ100", timeframe="tick"):
    return {"symbol": symbol, "price": price, "volume": 1, "timeframe": "tick",
            "timestamp": sequence, "sequence": sequence}


async def _run(ticks, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    for tk in ticks:
        await atom._on_tick(tk)
    out = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, out


async def test_all_clean():
    print("\n--- test_all_clean ---")
    _atom, _bus, out = await _run([_tick(10 + i, float(i)) for i in range(6)])
    last = out[-1]
    assert last["signal"] == "clean", (last["signal"], last["metadata"])
    assert last["metadata"]["quality_ratio"] == 1.0
    print("OK — كله صالح: clean (ratio=1.0)")


async def test_degraded_mixed():
    print("\n--- test_degraded_mixed ---")
    good = [_tick(10 + i, float(i)) for i in range(7)]
    bad = [_tick(None, float(7 + i)) for i in range(3)]
    _atom, _bus, out = await _run(good + bad)
    last = out[-1]
    assert last["signal"] == "degraded", (last["signal"], last["metadata"])
    assert "data_quality_degraded" in last["warnings"]
    print(f"OK — خليط خبيث: degraded (ratio={last['metadata']['quality_ratio']})")


async def test_counts_rejects():
    print("\n--- test_counts_rejects ---")
    _atom, _bus, out = await _run([_tick(10, 0.0), _tick(None, 1.0), _tick(-5, 2.0)])
    last = out[-1]
    m = last["metadata"]
    assert m["nan"] == 1, m
    assert m["nonpositive"] == 1, m
    assert m["rejected"] == 2, m
    print(f"OK — عدّ الرفض: nan={m['nan']} nonpos={m['nonpositive']} rejected={m['rejected']}")


async def test_duplicates():
    print("\n--- test_duplicates ---")
    _atom, _bus, out = await _run([_tick(10, 5.0), _tick(11, 5.0), _tick(12, 5.0)])
    m = out[-1]["metadata"]
    assert m["duplicates"] >= 2, m
    print(f"OK — مكرّر: duplicates={m['duplicates']}")


async def test_out_of_order():
    print("\n--- test_out_of_order ---")
    _atom, _bus, out = await _run([_tick(10, 5.0), _tick(11, 3.0)])
    m = out[-1]["metadata"]
    assert m["out_of_order"] >= 1, m
    print(f"OK — تراجع زمني: out_of_order={m['out_of_order']}")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _atom, _bus, out = await _run([_tick(10, 0.0), _tick(11, 1.0)])
    last = out[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص: {field}"
    for field in ("received", "valid", "rejected", "duplicates", "out_of_order",
                  "quality_ratio"):
        assert field in last["metadata"], f"حقل metadata ناقص: {field}"
    assert last["id"] == "quality"
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    await atom._on_tick(_tick(10, 0.0))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_all_clean, test_degraded_mixed, test_counts_rejects,
             test_duplicates, test_out_of_order, test_contract_shape_complete,
             test_health_states]
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
