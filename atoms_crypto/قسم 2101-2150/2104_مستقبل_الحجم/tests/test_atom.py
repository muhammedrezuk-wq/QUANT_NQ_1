import asyncio
import inspect
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom104", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom104"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_IN = _mod.EVENT_IN
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
        for h in self._handlers.get(name, []):
            r = h(payload)
            if inspect.isawaitable(r):
                await r

    def make_context(self):
        return AtomContext(atom_id=104, config={}, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus):
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    return atom


async def test_reshapes_volume():
    print("\n--- test_reshapes_volume ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish(EVENT_IN, {"symbol": "NQ", "volume": 1500, "timestamp": 7.0,
                                 "provider": "MT5"})
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert out == [{"symbol": "NQ", "volume": 1500.0, "timestamp": 7.0,
                    "provider": "MT5"}], out
    print(f"OK — أعاد تشكيل الحجم: {out[0]}")


async def test_ignores_without_symbol():
    print("\n--- test_ignores_without_symbol ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish(EVENT_IN, {"volume": 10})
    assert not [p for n, p in bus.published if n == EVENT_OUT]
    print("OK — حجم بلا symbol اتجاهل")


async def test_no_self_timestamp():
    print("\n--- test_no_self_timestamp ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish(EVENT_IN, {"symbol": "NQ", "volume": 5})
    out = [p for n, p in bus.published if n == EVENT_OUT][-1]
    assert "timestamp" not in out, "no self-stamped time (Rule 13)"
    print("OK — بلا timestamp ذاتي (قاعدة ١٣)")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await bus.publish(EVENT_IN, {"symbol": "NQ", "volume": 1})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")


async def main():
    tests = [test_reshapes_volume, test_ignores_without_symbol, test_no_self_timestamp,
             test_health_states]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e))); print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e))); print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}"); sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
