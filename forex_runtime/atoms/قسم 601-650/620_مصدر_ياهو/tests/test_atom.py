import asyncio
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
    "_atom620", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom620"] = _mod
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

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=620, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"symbols": ["^VIX", "DX-Y.NYB", "^TNX"],
       "base_url": "http://local.invalid/", "poll_interval_s": 30,
       "request_timeout_s": 6, "max_consecutive_errors": 5}


async def _make(prices):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    atom._fetch = lambda symbol: prices.get(symbol)  # محكم — بلا شبكة
    await atom.start()
    return bus, atom


async def _pulse(atom, t):
    await atom._on_pulse({"official_time": float(t)})
    if atom._poll_task is not None:
        await atom._poll_task


def _refs(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_publish_and_shape():
    print("\n--- test_publish_and_shape ---")
    bus, atom = await _make({"^VIX": 18.0, "DX-Y.NYB": 104.0, "^TNX": 4.2})
    await _pulse(atom, 100.0)
    refs = _refs(bus)
    assert len(refs) == 3, len(refs)
    o = refs[0]
    assert o["symbol"] == "^VIX" and o["value"] == 18.0 and o["provider"] == "yahoo", o
    print(f"OK — نشر 3 مؤشرات · العقد: {o}")


async def test_interval_gating():
    print("\n--- test_interval_gating ---")
    bus, atom = await _make({"^VIX": 18.0, "DX-Y.NYB": 104.0, "^TNX": 4.2})
    await _pulse(atom, 100.0)
    assert atom._last_poll_at == 100.0
    await _pulse(atom, 120.0)  # داخل الفاصل (< 130) → لا سحب
    assert atom._last_poll_at == 100.0, atom._last_poll_at
    await _pulse(atom, 131.0)  # مرّ الفاصل → سحب
    assert atom._last_poll_at == 131.0, atom._last_poll_at
    print("OK — الجدولة على النبضة: سحب كل poll_interval_s فقط")


async def test_dedup():
    print("\n--- test_dedup ---")
    bus, atom = await _make({"^VIX": 18.0, "DX-Y.NYB": 104.0, "^TNX": 4.2})
    await _pulse(atom, 100.0)
    n1 = atom._published
    await _pulse(atom, 200.0)  # نفس القيم → إسقاط تكرار
    assert atom._published == n1, atom._published
    assert atom._dropped_same >= 3, atom._dropped_same
    print(f"OK — إسقاط التكرار: published={n1} dropped_same={atom._dropped_same}")


async def test_no_fabrication_on_missing():
    print("\n--- test_no_fabrication_on_missing ---")
    bus, atom = await _make({"^VIX": None, "DX-Y.NYB": 0.0, "^TNX": 4.2})
    await _pulse(atom, 100.0)
    refs = _refs(bus)
    assert len(refs) == 1 and refs[0]["symbol"] == "^TNX", refs
    print("OK — قيمة ناقصة/≤0 لا تُنشر (لا اختلاق)")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    atom._fetch = lambda s: {"^VIX": 18.0, "DX-Y.NYB": 104.0, "^TNX": 4.2}.get(s)
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED  # NO_TICK_YET
    await _pulse(atom, 100.0)
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_repeated_failure_unhealthy():
    print("\n--- test_repeated_failure_unhealthy ---")
    def boom(symbol):
        raise RuntimeError("network down")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    atom._fetch = boom
    await atom.start()
    await _pulse(atom, 100.0)   # 3 أخطاء
    await _pulse(atom, 200.0)   # +3 → 6 ≥ 5
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    assert not _refs(bus)
    print("OK — فشل متتالٍ → UNHEALTHY بصمت (بلا نشر)")


async def main():
    tests = [test_publish_and_shape, test_interval_gating, test_dedup,
             test_no_fabrication_on_missing, test_health_states,
             test_repeated_failure_unhealthy]
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
