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
    "_atom108", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom108"] = _mod
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
        return AtomContext(atom_id=108, config={"source_event": "market.news",
                           "recent_size": 50}, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus):
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    return atom


def _out(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_normalizes_no_invented_score():
    print("\n--- test_normalizes_no_invented_score ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.news", {"id": "n1", "headline": "CPI beats",
                      "impact_level": "H", "symbols": ["EURUSD"]})  # no sentiment
    s = _out(bus)[-1]
    assert s["headline"] == "CPI beats" and s["impact_level"] == "HIGH"
    assert s["sentiment_score"] is None, "must NOT invent sentiment"
    print(f"OK — وحّد الأثر (H→HIGH)، وما اخترع sentiment (null): {s['impact_level']}")


async def test_dedupe():
    print("\n--- test_dedupe ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    ev = {"id": "n9", "headline": "Fed holds"}
    await bus.publish("market.news", ev)
    await bus.publish("market.news", ev)
    assert len(_out(bus)) == 1 and atom._rejected.get("duplicate") == 1
    print("OK — نفس الخبر (id) ما اتنشر مرّتين")


async def test_ignores_no_headline():
    print("\n--- test_ignores_no_headline ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    await bus.publish("market.news", {"id": "x", "source": "wire"})  # no headline
    assert not _out(bus) and atom._rejected.get("shape") == 1
    print("OK — خبر بلا عنوان اترفض")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await bus.publish("market.news", {"id": "n1", "headline": "x"})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY -> DEGRADED(UNAVAILABLE) -> HEALTHY")


async def main():
    tests = [test_normalizes_no_invented_score, test_dedupe, test_ignores_no_headline,
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
