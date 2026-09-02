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

_spec = _ilu.spec_from_file_location(
    "_atom110", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom110"] = _mod
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
        return AtomContext(atom_id=110, config={"source_event": "market.reference",
                           "watched_symbols": ["^VIX", "^TNX"], "min_change_pct": 1.0},
                           logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus):
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    return atom


def _out(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_publishes_with_change():
    print("\n--- test_publishes_with_change ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.reference", {"symbol": "^VIX", "value": 18.0, "timestamp": 3.0})
    await bus.publish("market.reference", {"symbol": "^VIX", "value": 20.0, "timestamp": 4.0})
    o = _out(bus)
    assert len(o) == 2, o  # first (baseline) + second (changed > 1%)
    assert o[1]["change_pct"] is not None and o[1]["value"] == 20.0
    assert o[1]["session_open"] == 18.0 and o[1]["timestamp"] == 4.0
    print(f"OK — نشر مع change_pct + session_open: change={o[1]['change_pct']}%")


async def test_below_min_change_skipped():
    print("\n--- test_below_min_change_skipped ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.reference", {"symbol": "^VIX", "value": 100.0})
    await bus.publish("market.reference", {"symbol": "^VIX", "value": 100.1})  # 0.1% < 1%
    assert len(_out(bus)) == 1, "tiny change not published"
    print("OK — تغيّر أصغر من العتبة ما اننشر")


async def test_not_watched_rejected():
    print("\n--- test_not_watched_rejected ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    await bus.publish("market.reference", {"symbol": "RANDOM", "value": 5.0})
    assert not _out(bus) and atom._rejected.get("not_watched") == 1
    print("OK — رمز غير مراقَب اترفض")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await bus.publish("market.reference", {"symbol": "^VIX", "value": 18.0})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")


async def main():
    tests = [test_publishes_with_change, test_below_min_change_skipped,
             test_not_watched_rejected, test_health_states]
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
