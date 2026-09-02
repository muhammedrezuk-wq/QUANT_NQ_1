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
    "_atom209", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom209"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OK = _mod.EVENT_OK
EVENT_FAIL = _mod.EVENT_FAIL


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

    def make_context(self, config):
        return AtomContext(atom_id=209, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _ext(sh=12, sl=8, status="ok"):
    return {"symbol": "NQ100", "id": "external", "cycle_id": "c1", "status": status,
            "signal": "none", "score": 0, "confidence": 0.0, "quality": "good",
            "warnings": [], "metadata": {"method": "swing_extension", "timeframe": "60s",
                                         "swing_high": sh, "swing_low": sl, "close": 10}}


def _collected(results, cycle="c1", symbol="NQ100"):
    return {"cycle_id": cycle, "symbol": symbol, "timeframe": "60s", "results": results,
            "expected": 8, "present": len(results), "complete": False}


async def _mk():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _by(bus, name):
    return [p for n, p in bus.published if n == name]


async def test_coherent_validates():
    print("\n--- test_coherent_validates ---")
    atom, bus = await _mk()
    await atom._on_collected(_collected({"external": _ext(12, 8)}))
    ok = _by(bus, EVENT_OK)
    assert ok and ok[-1]["validated"] is True
    assert not _by(bus, EVENT_FAIL)
    print("OK — بنية متّسقة → validated")


async def test_high_below_low_fails():
    print("\n--- test_high_below_low_fails ---")
    atom, bus = await _mk()
    await atom._on_collected(_collected({"external": _ext(5, 10)}))
    fail = _by(bus, EVENT_FAIL)
    assert fail and fail[-1]["reason"] == "external_high_below_low", fail
    print("OK — قمة تحت قاع → validation_failed")


async def test_negative_price_fails():
    print("\n--- test_negative_price_fails ---")
    atom, bus = await _mk()
    await atom._on_collected(_collected({"external": _ext(-1, 8)}))
    fail = _by(bus, EVENT_FAIL)
    assert fail and fail[-1]["reason"] == "external_high_not_positive"
    print("OK — سعر سالب → validation_failed")


async def test_no_external_fails():
    print("\n--- test_no_external_fails ---")
    # ملاحظة: كان اسمها test_no_external_validates وتثبت أنّ غياب 202
    # (external) نجاحٌ ضمنيّ — هذا هو العطل الذي أمرت ورقة «إغلاق منظومة
    # التحليل» (٢٠٢٦-٠٨-١٨ · §٢١) بإصلاحه: النقص = فشل صريح لا تمرير صامت.
    atom, bus = await _mk()
    await atom._on_collected(_collected({"swing": {"id": "swing", "status": "ok"}}))
    fail = _by(bus, EVENT_FAIL)
    assert fail and fail[-1]["reason"] == "external_missing", fail
    assert not _by(bus, EVENT_OK), "202 غائبة كليًّا ⇒ يجب ألّا يُنشَر validated"
    print("OK — بلا external (202) → فشل صريح external_missing")


async def test_external_not_ok_fails():
    print("\n--- test_external_not_ok_fails ---")
    atom, bus = await _mk()
    await atom._on_collected(
        _collected({"external": _ext(status="insufficient_data")}))
    fail = _by(bus, EVENT_FAIL)
    assert fail and fail[-1]["reason"] == "external_not_ok", fail
    assert not _by(bus, EVENT_OK)
    print("OK — external بحالة غير ok → فشل صريح external_not_ok")


async def test_required_only_cycle_validates():
    print("\n--- test_required_only_cycle_validates ---")
    # (أ) دورة فيها 201/202/203/207 فقط (بلا 204-208 الاختياريّة) يجب أن
    # تُصادَق — 209 لا يفحص إلّا 202، وحضورها بحالة صحيحة كافٍ.
    atom, bus = await _mk()
    await atom._on_collected(_collected({
        "swing": {"id": "swing", "status": "ok"},
        "external": _ext(12, 8),
        "internal": {"id": "internal", "status": "ok"},
        "structure_trend": {"id": "structure_trend", "status": "ok"}}))
    ok = _by(bus, EVENT_OK)
    assert ok and ok[-1]["validated"] is True
    assert not _by(bus, EVENT_FAIL)
    print("OK — دورة بالإلزاميّات الأربع فقط (بلا 204-208) → validated")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    await atom._on_collected(_collected({"external": _ext(12, 8)}))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_coherent_validates,
        test_high_below_low_fails,
        test_negative_price_fails,
        test_no_external_fails,
        test_external_not_ok_fails,
        test_required_only_cycle_validates,
        test_health_states,
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
