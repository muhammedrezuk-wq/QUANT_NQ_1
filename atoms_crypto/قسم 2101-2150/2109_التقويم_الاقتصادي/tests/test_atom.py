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
    "_atom109", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom109"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_UPCOMING = _mod.EVENT_UPCOMING
EVENT_WINDOW = _mod.EVENT_WINDOW


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
        return AtomContext(atom_id=109, config={"source_event": "market.calendar",
                           "alert_before_seconds": 900.0, "keep_past_seconds": 3600.0,
                           "min_impact": "LOW"}, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus):
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    return atom


async def test_announces_in_window_once():
    print("\n--- test_announces_in_window_once ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("market.calendar", {"id": "cpi", "title": "US CPI",
                      "scheduled_at": 10000.0, "impact_level": "HIGH"})
    await bus.publish("SYS_SECOND", {"official_time": 8000.0})  # 2000s away > 900 window
    assert not [p for n, p in bus.published if n == EVENT_UPCOMING], "not yet in window"
    await bus.publish("SYS_SECOND", {"official_time": 9200.0})  # 800s away, in window
    ann = [p for n, p in bus.published if n == EVENT_UPCOMING]
    assert len(ann) == 1 and ann[0]["title"] == "US CPI"
    assert abs(ann[0]["seconds_until"] - 800.0) < 1e-6, ann[0]
    win = [p for n, p in bus.published if n == EVENT_WINDOW]
    assert win and win[-1]["in_event_window"] is True
    await bus.publish("SYS_SECOND", {"official_time": 9300.0})  # still in window
    assert len([p for n, p in bus.published if n == EVENT_UPCOMING]) == 1, "announced once"
    print(f"OK — أعلن الحدث داخل النافذة مرّة (بوقت SYS_SECOND): seconds_until={ann[0]['seconds_until']}")


async def test_drops_past_events():
    print("\n--- test_drops_past_events ---")
    bus = FakeEventBus()
    atom = await _make(bus)
    await bus.publish("market.calendar", {"id": "old", "title": "Old", "scheduled_at": 100.0})
    await bus.publish("SYS_SECOND", {"official_time": 100000.0})  # far past + keep
    assert len(atom._events) == 0, "past event dropped"
    print("OK — الحدث الماضي (تجاوز keep_past) اتشال")


async def test_impact_filter():
    print("\n--- test_impact_filter ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(AtomContext(atom_id=109, config={"source_event": "market.calendar",
                          "alert_before_seconds": 900.0, "keep_past_seconds": 3600.0,
                          "min_impact": "HIGH"}, logger=_NullLogger(),
                          publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    await bus.publish("market.calendar", {"id": "low", "title": "minor",
                      "scheduled_at": 10000.0, "impact_level": "LOW"})
    assert len(atom._events) == 0, "below min_impact dropped"
    await bus.publish("market.calendar", {"id": "hi", "title": "major",
                      "scheduled_at": 10000.0, "impact_level": "HIGH"})
    assert len(atom._events) == 1
    print("OK — مرشّح الأثر: LOW اتجاهل (min=HIGH)، HIGH اتتبّع")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED  # no time
    await bus.publish("SYS_SECOND", {"official_time": 100.0})
    await bus.publish("market.calendar", {"id": "x", "title": "E", "scheduled_at": 10000.0})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY -> DEGRADED(NO_TIME) -> HEALTHY")


async def main():
    tests = [test_announces_in_window_once, test_drops_past_events, test_impact_filter,
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
