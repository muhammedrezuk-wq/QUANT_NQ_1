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
    "_atom304", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom304"] = _mod
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
        self._handlers = {}

    def subscribe(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=304, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tick(price, sequence=0.0, symbol="NQ100", timeframe="60s"):
    return {"symbol": symbol, "price": price, "volume": 1, "timeframe": "tick",
            "timestamp": sequence}


async def _run(closes, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or {"window_size": 20}))
    await atom.start()
    for i, c in enumerate(closes):
        await atom._on_tick(_tick(c, sequence=i))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, out


async def test_insufficient_one_point():
    print("\n--- test_insufficient_one_point ---")
    _atom, _bus, out = await _run([10])
    last = out[-1]
    assert last["status"] == "insufficient_data"
    assert "insufficient_data_points" in last["warnings"]
    print("OK — نقطة واحدة: insufficient (الانحراف بدّه ≥2)")


async def test_std_computed():
    print("\n--- test_std_computed ---")
    # [2,4,6]: mean=4, var=((−2)²+0+2²)/(3-1)=8/2=4, std=2
    _atom, _bus, out = await _run([2, 4, 6])
    last = out[-1]
    assert last["status"] == "ok"
    assert last["metadata"]["value"] == 2, last["metadata"]["value"]
    print(f"OK — انحراف [2,4,6] = {last['metadata']['value']} (عيّنة n-1)")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _atom, _bus, out = await _run([2, 4, 6])
    last = out[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص: {field}"
    assert "value" in last["metadata"]
    assert last["id"] == "std"
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"window_size": 20}))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    await atom._on_tick(_tick(10))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_insufficient_one_point, test_std_computed,
             test_contract_shape_complete, test_health_states]
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
