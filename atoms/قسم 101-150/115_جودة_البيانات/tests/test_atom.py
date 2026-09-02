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
    "_atom115", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom115"] = _mod
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
        return AtomContext(atom_id=115, config={"gap_threshold_s": 5.0,
                           "spike_pct": 5.0, "spread_pct": 1.0}, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus):
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    await bus.publish("platform.account.state", {"account_id": "A", "broker": "BR"})
    return atom


def _alerts(bus, kind=None):
    out = [p for n, p in bus.published if n == EVENT_OUT]
    return [a for a in out if kind is None or a["kind"] == kind]


async def test_clean_tick_no_alert():
    print("\n--- test_clean_tick_no_alert ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.tick", {"account_id": "A", "symbol": "NQ", "bid": 100.0, "ask": 100.2, "timestamp": 1.0})
    assert not _alerts(bus), "clean tick -> no alert"
    print("OK — تكّة سليمة → لا تنبيه")


async def test_detects_spike():
    print("\n--- test_detects_spike ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.tick", {"account_id": "A", "symbol": "NQ", "bid": 100.0, "ask": 100.2, "timestamp": 1.0})
    await bus.publish("market.tick", {"account_id": "A", "symbol": "NQ", "bid": 110.0, "ask": 110.2, "timestamp": 2.0})  # ~10%
    sp = _alerts(bus, "price_spike")
    assert sp and sp[-1]["symbol"] == "NQ" and sp[-1]["timestamp"] == 2.0, sp
    print(f"OK — كشف قفزة سعرية: {sp[-1]['change_pct']}%")


async def test_detects_gap():
    print("\n--- test_detects_gap ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.tick", {"account_id": "A", "symbol": "NQ", "bid": 100.0, "ask": 100.2, "timestamp": 1.0})
    await bus.publish("market.tick", {"account_id": "A", "symbol": "NQ", "bid": 100.0, "ask": 100.2, "timestamp": 20.0})  # 19s gap
    assert _alerts(bus, "time_gap"), "gap > 5s must alert"
    print("OK — كشف فجوة زمنية (>5s)")


async def test_detects_abnormal_spread():
    print("\n--- test_detects_abnormal_spread ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.tick", {"account_id": "A", "symbol": "NQ", "bid": 100.0, "ask": 105.0, "timestamp": 1.0})  # 5% spread
    assert _alerts(bus, "abnormal_spread"), "wide spread must alert"
    print("OK — كشف سبريد شاذ")


async def test_per_symbol_no_false_spike():
    print("\n--- test_per_symbol_no_false_spike ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.tick", {"account_id": "A", "symbol": "BTC", "bid": 64000.0, "ask": 64001.0, "timestamp": 1.0})
    await bus.publish("market.tick", {"account_id": "A", "symbol": "ETH", "bid": 2500.0, "ask": 2500.5, "timestamp": 2.0})
    assert not _alerts(bus, "price_spike"), "different symbols must NOT cross-trigger spike"
    print("OK — حالة مستقلة لكل رمز (BTC↔ETH ما يعملوا قفزة وهمية)")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await bus.publish("platform.account.state", {"account_id": "A", "broker": "BR"})
    await bus.publish("market.tick", {"account_id": "A", "symbol": "NQ", "bid": 1, "ask": 1.001, "timestamp": 1.0})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")


async def main():
    tests = [test_clean_tick_no_alert, test_detects_spike, test_detects_gap,
             test_detects_abnormal_spread, test_per_symbol_no_false_spike,
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
