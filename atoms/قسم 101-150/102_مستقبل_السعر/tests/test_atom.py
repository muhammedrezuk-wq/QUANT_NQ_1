import asyncio
import inspect
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
from pathlib import Path as _AtomPath  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom102", _AtomPath(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom102"] = _mod
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
        for h in self._handlers.get(name, []):
            r = h(payload)
            if inspect.isawaitable(r):
                await r

    def make_context(self):
        return AtomContext(atom_id=102, config={}, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def test_reshapes_tick_to_price():
    print("\n--- test_reshapes_tick_to_price ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    await bus.publish("market.tick.validated", {"symbol": "NQ", "bid": 100.0, "ask": 100.5, "timestamp": 7.0, "provider": "MT5"})
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert len(out) == 1
    assert out[0]["symbol"] == "NQ" and out[0]["bid"] == 100.0 and out[0]["timestamp"] == 7.0
    assert out[0]["provider"] == "MT5"
    print(f"OK — أعاد تشكيل التكّة → {EVENT_OUT}: {out[0]}")


async def test_no_self_stamped_timestamp():
    """قاعدة 13 — بلا timestamp بالتكّة: ما تختم بساعتها (تحذفه، الناقل يختمه)."""
    print("\n--- test_no_self_stamped_timestamp ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    await bus.publish("market.tick.validated", {"symbol": "NQ", "bid": 1, "ask": 2})  # لا timestamp
    out = [p for n, p in bus.published if n == EVENT_OUT][-1]
    assert "timestamp" not in out, "ما تخترع timestamp (قاعدة 13)"
    print("OK — بلا timestamp ذاتي (الناقل يختمه)")


async def test_ignores_tick_without_symbol():
    print("\n--- test_ignores_tick_without_symbol ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    await bus.publish("market.tick.validated", {"bid": 1, "ask": 2})  # لا symbol
    assert not [p for n, p in bus.published if n == EVENT_OUT]
    print("OK — تكّة بلا symbol اتجاهلت")


async def main():
    tests = [test_reshapes_tick_to_price, test_no_self_stamped_timestamp, test_ignores_tick_without_symbol]
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
