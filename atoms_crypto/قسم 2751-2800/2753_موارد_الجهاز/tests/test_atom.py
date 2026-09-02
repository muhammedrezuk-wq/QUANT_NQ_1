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
    "_atom753", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom753"] = _mod
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

    def make_context(self, cfg):
        return AtomContext(atom_id=753, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _make(bus, enable_temp=True):
    atom = Atom()
    await atom.initialize(bus.make_context({"enable_temperature": enable_temp}))
    await atom.start()
    return atom


async def test_publishes_raw_metrics_on_pulse():
    print("\n--- test_publishes_raw_metrics_on_pulse ---")
    bus = FakeEventBus()
    await _make(bus, enable_temp=False)
    await bus.publish(EVENT_IN, {"official_time": 1234.0})
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert len(out) == 1, out
    body = out[0]
    # raw numeric measurements (not interpretation)
    assert isinstance(body.get("cpu_pct"), (int, float)), body
    assert isinstance(body.get("memory_pct"), (int, float)), body
    assert body["state"] == "HEALTHY" and body["timestamp"] == 1234.0, body
    assert EVENT_OUT.endswith(".state"), "replayable latest reading"
    print(f"OK — قياس خام على SYS_5MIN: cpu={body['cpu_pct']}% mem={body['memory_pct']}%")


async def test_no_interpretation_no_thresholds():
    print("\n--- test_no_interpretation_no_thresholds ---")
    bus = FakeEventBus()
    await _make(bus, enable_temp=False)
    await bus.publish(EVENT_IN, {"official_time": 1.0})
    body = [p for n, p in bus.published if n == EVENT_OUT][-1]
    # atom reports facts only; must NOT emit a verdict like danger/warning/trend
    for banned in ("danger", "warning", "alert", "trend", "critical"):
        assert banned not in body, f"raw sensor must not interpret: {banned}"
    print("OK — حقائق خام فقط، بلا حكم/عتبة/اتجاه (التفسير للحوكمة)")


async def test_health_reports_readings():
    print("\n--- test_health_reports_readings ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"enable_temperature": False}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY  # not started
    await atom.start()
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY, h.state
    assert "cpu_pct" in (h.details or {}), h.details
    print(f"OK — الصحة تقرأ المؤشرات الحيّة: {h.message}")


async def test_timestamp_from_pulse_only():
    print("\n--- test_timestamp_from_pulse_only ---")
    bus = FakeEventBus()
    await _make(bus, enable_temp=False)
    await bus.publish(EVENT_IN, {})  # no official_time
    body = [p for n, p in bus.published if n == EVENT_OUT][-1]
    assert "timestamp" not in body, "no self-stamped time (Rule 13)"
    print("OK — بلا official_time: ما يخترع وقتًا (قاعدة ١٣ — الناقل يختم)")


async def main():
    tests = [test_publishes_raw_metrics_on_pulse, test_no_interpretation_no_thresholds,
             test_health_reports_readings, test_timestamp_from_pulse_only]
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
