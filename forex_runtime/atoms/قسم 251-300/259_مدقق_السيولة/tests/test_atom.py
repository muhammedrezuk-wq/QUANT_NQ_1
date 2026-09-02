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
    "_atom259", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom259"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OK = _mod.EVENT_OK
EVENT_FAIL = _mod.EVENT_FAIL


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
        return AtomContext(atom_id=259, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _pool(price, status="ok"):
    return {"symbol": "NQ100", "id": "pool", "cycle_id": "c", "status": status,
            "signal": "pool_high", "score": 0, "confidence": 1.0, "quality": "good",
            "warnings": [], "metadata": {"method": "swing_as_pool", "timeframe": "60s",
                                         "side": "high", "price": price}}


def _collected(results):
    return {"cycle_id": "c", "symbol": "NQ100", "timeframe": "60s", "results": results,
            "expected": 5, "present": len(results), "complete": False}


async def _mk():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _by(bus, name):
    return [p for n, p in bus.published if n == name]


async def test_coherent_validates():
    print("\n--- test_coherent_validates ---")
    atom, bus = await _mk()
    await atom._on_collected(_collected({"pool": _pool(12)}))
    assert _by(bus, EVENT_OK) and not _by(bus, EVENT_FAIL)
    print("OK — سيولة متّسقة → validated")


async def test_negative_pool_fails():
    print("\n--- test_negative_pool_fails ---")
    atom, bus = await _mk()
    await atom._on_collected(_collected({"pool": _pool(-5)}))
    fail = _by(bus, EVENT_FAIL)
    assert fail and fail[-1]["reason"] == "pool_price_not_positive"
    print("OK — سعر بركة سالب → validation_failed")


async def test_no_pool_validates():
    print("\n--- test_no_pool_validates ---")
    atom, bus = await _mk()
    await atom._on_collected(_collected({"fvg": {"id": "fvg", "status": "ok"}}))
    assert _by(bus, EVENT_OK)
    print("OK — بلا بركة → validated (لا تناقض)")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    await atom._on_collected(_collected({"pool": _pool(12)}))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_coherent_validates, test_negative_pool_fails,
             test_no_pool_validates, test_health_states]
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
