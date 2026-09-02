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
    "_atom314", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom314"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"window_size": 20, "bin_count": 10, "tail_ratio": 0.25}


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
        return AtomContext(atom_id=314, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tick(price, sequence=0.0, symbol="NQ100", timeframe="60s"):
    return {"symbol": symbol, "price": price, "volume": 1, "timeframe": "tick",
            "timestamp": sequence}


async def _run(closes, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    for i, c in enumerate(closes):
        await atom._on_tick(_tick(c, sequence=i))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, out


async def test_insufficient():
    print("\n--- test_insufficient ---")
    _atom, _bus, out = await _run([10, 11])
    assert out[-1]["status"] == "insufficient_data", out[-1]["status"]
    print("OK — نقاط قليلة: insufficient")


async def test_peak_last_in_dense():
    print("\n--- test_peak_last_in_dense ---")
    _atom, _bus, out = await _run([10, 10, 10, 11, 12, 10])
    last = out[-1]
    assert last["signal"] == "peak", (last["signal"], last["metadata"])
    print(f"OK — الأخير بالكثيف: peak (modal={last['metadata']['modal_bin']})")


async def test_tail_last_is_outlier():
    print("\n--- test_tail_last_is_outlier ---")
    _atom, _bus, out = await _run([10, 10, 10, 10, 10, 10, 10, 10, 10, 100])
    last = out[-1]
    assert last["signal"] == "tail", (last["signal"], last["metadata"])
    print(f"OK — شاذّ أخير: tail (latest={last['metadata']['latest_bin']})")


async def test_zero_range():
    print("\n--- test_zero_range ---")
    _atom, _bus, out = await _run([10, 10, 10, 10])
    last = out[-1]
    assert last["signal"] == "peak", last["signal"]
    assert "zero_range" in last["warnings"], last["warnings"]
    print("OK — قيم متطابقة: peak + zero_range")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _atom, _bus, out = await _run([10, 12, 11, 13, 10, 12])
    last = out[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص: {field}"
    for field in ("counts", "modal_bin", "latest_bin", "min", "max"):
        assert field in last["metadata"], f"حقل metadata ناقص: {field}"
    assert last["id"] == "distribution"
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
    await atom._on_tick(_tick(10))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_insufficient, test_peak_last_in_dense, test_tail_last_is_outlier,
             test_zero_range, test_contract_shape_complete, test_health_states]
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
