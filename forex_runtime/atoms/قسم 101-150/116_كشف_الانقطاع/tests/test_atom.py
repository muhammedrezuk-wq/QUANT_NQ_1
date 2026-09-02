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
    "_atom116", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom116"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_INTERRUPTED = _mod.EVENT_INTERRUPTED
EVENT_RECOVERED = _mod.EVENT_RECOVERED


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
        return AtomContext(atom_id=116, config={"max_silence_seconds": 10.0},
                           logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus):
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    return atom


def _events(bus, name):
    return [p for n, p in bus.published if n == name]


async def test_interrupt_and_recover():
    print("\n--- test_interrupt_and_recover ---")
    bus = FakeEventBus()
    await _make(bus)
    # baseline pulse + a feed tick keep it alive
    await bus.publish("SYS_SECOND", {"official_time": 100.0})
    await bus.publish("feed.mt5.tick", {"symbol": "NQ"})     # last_activity=100
    await bus.publish("SYS_SECOND", {"official_time": 105.0})  # 5s silence < 10 -> ok
    assert not _events(bus, EVENT_INTERRUPTED)
    await bus.publish("SYS_SECOND", {"official_time": 120.0})  # 20s silence > 10 -> interrupt
    itr = _events(bus, EVENT_INTERRUPTED)
    assert len(itr) == 1 and itr[0]["timestamp"] == 120.0, itr
    await bus.publish("SYS_SECOND", {"official_time": 121.0})  # still silent, no dup
    assert len(_events(bus, EVENT_INTERRUPTED)) == 1, "interrupt fires once"
    await bus.publish("feed.mt5.tick", {"symbol": "NQ"})       # activity resumes
    await bus.publish("SYS_SECOND", {"official_time": 122.0})  # recovered
    assert _events(bus, EVENT_RECOVERED), "recovery signalled"
    print("OK — كشف الانقطاع (بوقت SYS_SECOND، بلا time.time) + التعافي")


async def test_no_false_interrupt_before_baseline():
    print("\n--- test_no_false_interrupt_before_baseline ---")
    bus = FakeEventBus()
    await _make(bus)
    await bus.publish("SYS_SECOND", {"official_time": 1000.0})  # first pulse = baseline
    assert not _events(bus, EVENT_INTERRUPTED), "no false interrupt on first pulse"
    print("OK — أول نبضة = خط أساس، بلا إنذار كاذب")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED  # no time yet
    await bus.publish("SYS_SECOND", {"official_time": 100.0})
    await bus.publish("feed.mt5.tick", {"symbol": "NQ"})
    await bus.publish("SYS_SECOND", {"official_time": 101.0})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    await bus.publish("SYS_SECOND", {"official_time": 200.0})  # silence -> interrupted
    assert (await atom.health_check()).state == HealthState.DEGRADED
    print("OK — الصحة: UNHEALTHY -> DEGRADED(NO_TIME) -> HEALTHY -> DEGRADED(انقطاع)")


async def main():
    tests = [test_interrupt_and_recover, test_no_false_interrupt_before_baseline,
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
