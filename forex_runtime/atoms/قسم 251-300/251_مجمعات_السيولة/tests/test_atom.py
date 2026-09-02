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
    "_atom251", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom251"] = _mod
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

    def make_context(self, config):
        return AtomContext(atom_id=251, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _swing(signal, price=None, close=100.0, ts=0.0, score=0, symbol="NQ100", tf="60s"):
    meta = {"timeframe": tf, "close": close}
    if price is not None:
        meta["price"] = price
        meta["swing_time"] = ts
    return {"symbol": symbol, "id": "swing", "cycle_id": "%s|%s|%s" % (symbol, tf, ts),
            "status": "ok", "signal": signal, "score": score,
            "confidence": 1.0 if price is not None else 0.0,
            "quality": "good", "warnings": [], "metadata": meta}


async def _run(swings):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    for s in swings:
        await atom._on_swing(s)
    pools = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, pools


async def test_swing_high_becomes_pool_high():
    print("\n--- test_swing_high_becomes_pool_high ---")
    _atom, _bus, pools = await _run([_swing("swing_high", 12, ts=2, score=67)])
    last = pools[-1]
    assert last["signal"] == "pool_high", last["signal"]
    assert last["metadata"]["side"] == "high"
    assert last["metadata"]["price"] == 12
    assert last["score"] == 67 and last["confidence"] == 0.67, last["confidence"]
    print(f"OK — قمة → بركة شراء: side=high price={last['metadata']['price']} "
          f"confidence={last['confidence']}")


async def test_swing_low_becomes_pool_low():
    print("\n--- test_swing_low_becomes_pool_low ---")
    _atom, _bus, pools = await _run([_swing("swing_low", 8, ts=2, score=55)])
    last = pools[-1]
    assert last["signal"] == "pool_low", last["signal"]
    assert last["metadata"]["side"] == "low"
    assert last["metadata"]["price"] == 8
    assert last["score"] == 55 and last["confidence"] == 0.55, last["confidence"]
    print(f"OK — قاع → بركة بيع: side=low price={last['metadata']['price']} "
          f"confidence={last['confidence']}")


async def test_confidence_scales_with_prominence():
    print("\n--- test_confidence_scales_with_prominence ---")
    # §12.3 — الثقة لم تعد ثنائية 1.0/0.0: بروز مختلف ⇒ ثقة مختلفة، لا رقم مسطَّح.
    _atom, _bus, weak = await _run([_swing("swing_high", 12, ts=2, score=20)])
    _atom, _bus, strong = await _run([_swing("swing_high", 12, ts=2, score=90)])
    assert weak[-1]["confidence"] == 0.2, weak[-1]["confidence"]
    assert strong[-1]["confidence"] == 0.9, strong[-1]["confidence"]
    assert weak[-1]["confidence"] != strong[-1]["confidence"]
    print(f"OK — بروز 20→ثقة {weak[-1]['confidence']} · بروز 90→ثقة {strong[-1]['confidence']} (متدرّجة لا ثنائية)")


async def test_none_swing_no_pool():
    print("\n--- test_none_swing_no_pool ---")
    _atom, _bus, pools = await _run([_swing("none")])
    last = pools[-1]
    assert last["signal"] == "none"
    assert last["confidence"] == 0.0
    print("OK — لا سوينغ = لا بركة")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _atom, _bus, pools = await _run([_swing("swing_high", 12, ts=2, score=67)])
    last = pools[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص بالعقد: {field}"
    for field in ("method", "timeframe", "side", "price", "pool_time", "close"):
        assert field in last["metadata"], f"حقل metadata ناقص: {field}"
    assert last["id"] == "pool"
    print("OK — العقد الموحّد كامل الحقول")


async def test_score_boundaries_no_crash_correct_confidence():
    print("\n--- test_score_boundaries_no_crash_correct_confidence ---")
    # بند 26 (فحص من الصفر، لا نصّ تدقيق أصليّ متاح): 251 تحوّل score
    # لـ int() بلا try/except، بخلاف كل تحويل رقميّ آخر بالملف -- تحقّقت
    # أن المنتِج الحقيقيّ الوحيد (201) يرسل score كعدد صحيح 0..100 دومًا
    # (int(round(...)) قبل النشر)، فلا مسار حيّ يبعث None أو نصًّا هناك.
    # هذا الاختبار يقفل الطرفين الحقيقيّين اللذين لا يغطّيهما ملف الاختبار
    # الحالي: بروز صفريّ (score=0 مع إشارة حقيقية) وبروز أقصى (score=100).
    _atom, _bus, zero = await _run([_swing("swing_high", 12, ts=2, score=0)])
    assert zero[-1]["score"] == 0 and zero[-1]["confidence"] == 0.0, zero[-1]
    _atom, _bus, full = await _run([_swing("swing_low", 8, ts=2, score=100)])
    assert full[-1]["score"] == 100 and full[-1]["confidence"] == 1.0, full[-1]
    print("OK — score=0 وscore=100 (طرفا مدى المنتِج الحقيقيّ 201): بلا سقوط، ثقة صحيحة عند الحدّين")


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
    await atom._on_swing(_swing("swing_high", 12, ts=1))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_swing_high_becomes_pool_high,
        test_swing_low_becomes_pool_low,
        test_confidence_scales_with_prominence,
        test_none_swing_no_pool,
        test_contract_shape_complete,
        test_score_boundaries_no_crash_correct_confidence,
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
