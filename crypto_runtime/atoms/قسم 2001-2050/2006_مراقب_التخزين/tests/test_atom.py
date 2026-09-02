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
from pathlib import Path as _AtomPath  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom006", _AtomPath(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom006"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_LOW = _mod.EVENT_LOW
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
        for handler in self._handlers.get(name, []):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def make_context(self, config):
        return AtomContext(atom_id=6, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def test_low_alert_edge_triggered_once():
    print("\n--- test_low_alert_edge_triggered_once ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"path": ".", "warn_threshold_pct": 0, "critical_threshold_pct": 0}))
    await atom.start(); await bus.publish(_mod.EVENT_PULSE, {})
    h1 = await atom.health_check()
    assert h1.state == HealthState.UNHEALTHY
    lows = [p for n, p in bus.published if n == EVENT_LOW]
    assert len(lows) == 1, "تنبيه واحد عند أول تجاوز"
    await atom.health_check()  # reporting only: must not publish
    await bus.publish(_mod.EVENT_PULSE, {})
    lows2 = [p for n, p in bus.published if n == EVENT_LOW]
    assert len(lows2) == 1, "ما يتكرّر بالبقاء بنفس الحالة (edge)"
    print(f"OK — نُشر {EVENT_LOW} مرّة واحدة بس رغم فحصين")


async def test_recovered_when_back_healthy():
    print("\n--- test_recovered_when_back_healthy ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"path": ".", "warn_threshold_pct": 0, "critical_threshold_pct": 0}))
    await atom.start(); await bus.publish(_mod.EVENT_PULSE, {})  # low
    atom._warn_threshold_pct = 100.0
    atom._critical_threshold_pct = 100.0
    await bus.publish(_mod.EVENT_PULSE, {})
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY
    assert [p for n, p in bus.published if n == EVENT_RECOVERED], "لازم ينشر recovered عند العودة"
    print(f"OK — نُشر {EVENT_RECOVERED} عند العودة لسليم")


async def test_bad_path_reports_unknown_without_crash():
    """حالة فشل (قاعدة 9) — مسار غير موجود: UNKNOWN بلا انهيار."""
    print("\n--- test_bad_path_reports_unknown_without_crash ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"path": os.path.join("Z:\\", "no_such_dir_xyz"), "warn_threshold_pct": 80, "critical_threshold_pct": 90}))
    await atom.start(); await bus.publish(_mod.EVENT_PULSE, {})
    h = await atom.health_check()
    assert h.state == HealthState.UNKNOWN, "مسار غلط = UNKNOWN"
    print(f"OK — مسار غلط: UNKNOWN بلا انهيار ({h.message[:40]}...)")


async def main():
    tests = [
        test_low_alert_edge_triggered_once,
        test_recovered_when_back_healthy,
        test_bad_path_reports_unknown_without_crash,
    ]
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
