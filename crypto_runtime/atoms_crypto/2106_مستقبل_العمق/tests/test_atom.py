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
    "_atom106", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom106"] = _mod
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
        return AtomContext(atom_id=106, config={"source_event": "market.depth",
                           "max_levels": 10}, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus):
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    return atom


def _out(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_valid_book_derivatives():
    print("\n--- test_valid_book_derivatives ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.depth", {"symbol": "NQ",
                      "bids": [[100.0, 5], [99.0, 3]], "asks": [[101.0, 4], [102.0, 2]],
                      "timestamp": 7.0})
    o = _out(bus)
    assert len(o) == 1, o
    s = o[0]
    assert s["best_bid"] == 100.0 and s["best_ask"] == 101.0 and s["spread"] == 1.0
    assert s["mid"] == 100.5 and s["bid_volume"] == 8 and s["ask_volume"] == 6
    assert abs(s["imbalance"] - (2/14)) < 1e-6 and s["timestamp"] == 7.0
    print(f"OK — دفتر صالح + مشتقات (mid/spread/imbalance): imbalance={s['imbalance']}")


async def test_rejects_crossed():
    print("\n--- test_rejects_crossed ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    await bus.publish("market.depth", {"symbol": "NQ", "bids": [[102.0, 1]],
                                       "asks": [[101.0, 1]]})  # bid >= ask
    assert not _out(bus) and atom._rejected.get("crossed_book") == 1
    print("OK — دفتر متقاطع (bid≥ask) اترفض")


async def test_rejects_unsorted():
    print("\n--- test_rejects_unsorted ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    await bus.publish("market.depth", {"symbol": "NQ",
                      "bids": [[99.0, 1], [100.0, 1]], "asks": [[101.0, 1]]})  # bids ↑
    assert not _out(bus) and atom._rejected.get("unsorted") == 1
    print("OK — دفتر غير مرتّب اترفض (يمنع تسميم المستهلكين)")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED  # no source
    await bus.publish("market.depth", {"symbol": "NQ", "bids": [[100.0, 1]],
                                       "asks": [[101.0, 1]]})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY -> DEGRADED(UNAVAILABLE) -> HEALTHY")


async def main():
    tests = [test_valid_book_derivatives, test_rejects_crossed, test_rejects_unsorted,
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
