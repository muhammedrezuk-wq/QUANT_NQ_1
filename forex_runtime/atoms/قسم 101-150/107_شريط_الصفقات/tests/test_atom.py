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
    "_atom107", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom107"] = _mod
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

    def make_context(self, every=2, window=500):
        return AtomContext(atom_id=107, config={"source_event": "market.trade",
                           "window_size": window, "publish_every_n_trades": every},
                           logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus, every=2):
    atom = Atom()
    await atom.initialize(bus.make_context(every=every))
    await atom.start()
    return atom


def _out(bus):
    rows = [p for n, p in bus.published if n == EVENT_OUT]
    # إعلان السكون الصادق (DORMANT — «لا ناشر لمصدر الشريط») ليس تحديث شريط:
    # الذرّة تعلنه بشفافية عند الإقلاع، والحارس يفحص تحديثات البيانات وحدها.
    return [p for p in rows
            if str(p.get("state") or (p.get("unified") or {}).get("state") or "").upper()
            != "DORMANT"]


async def test_window_and_ratio():
    print("\n--- test_window_and_ratio ---")
    bus = FakeEventBus()
    await _make(bus, every=2)
    await bus.publish("market.trade", {"symbol": "NQ", "price": 100, "size": 3, "side": "BUY"})
    assert not _out(bus), "publishes every 2 trades"
    await bus.publish("market.trade", {"symbol": "NQ", "price": 101, "size": 1, "side": "SELL"})
    o = _out(bus)
    assert len(o) == 1, o
    s = o[0]
    assert s["trades_in_window"] == 2 and s["buy_volume"] == 3 and s["sell_volume"] == 1
    assert s["last_price"] == 101 and abs(s["buy_ratio"] - 0.75) < 1e-6, s
    print(f"OK — نافذة متحركة + نسبة الشراء: buy_ratio={s['buy_ratio']}")


async def test_unknown_side_not_guessed():
    print("\n--- test_unknown_side_not_guessed ---")
    bus = FakeEventBus()
    await _make(bus, every=1)
    await bus.publish("market.trade", {"symbol": "NQ", "price": 100, "size": 5})  # no side
    s = _out(bus)[-1]
    assert s["unknown_volume"] == 5 and s["buy_ratio"] is None, s
    print("OK — اتجاه مجهول ما يُخمَّن (unknown_volume=5، buy_ratio=null)")


async def test_rejects_bad_price():
    print("\n--- test_rejects_bad_price ---")
    bus = FakeEventBus()
    atom = await _make(bus, every=1)
    await bus.publish("market.trade", {"symbol": "NQ", "price": -1, "size": 5})
    assert not _out(bus) and atom._rejected.get("bad_price") == 1
    print("OK — سعر غير صالح اترفض")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(every=1))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await bus.publish("market.trade", {"symbol": "NQ", "price": 100, "size": 1, "side": "BUY"})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY -> DEGRADED(UNAVAILABLE) -> HEALTHY")


async def main():
    tests = [test_window_and_ratio, test_unknown_side_not_guessed, test_rejects_bad_price,
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
