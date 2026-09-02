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
    "_atom257", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom257"] = _mod
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
        return AtomContext(atom_id=257, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _delta(delta=None, ratio=None, symbol="NQ100", tf="60s"):
    meta = {"timeframe": tf}
    if delta is not None:
        meta["delta"] = delta
    if ratio is not None:
        meta["ratio"] = ratio
    return {"symbol": symbol, "id": "delta", "cycle_id": "c", "status": "ok",
            "signal": "buy_pressure", "score": 0,
            "confidence": abs(ratio) if ratio is not None else 0.0, "quality": "good",
            "warnings": [], "metadata": meta}


async def _mk():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _out(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_unavailable_by_default():
    print("\n--- test_unavailable_by_default ---")
    atom, _bus = await _mk()
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED
    assert h.message == "ORDER_FLOW_UNAVAILABLE"
    print("OK — بلا دلتا (256 مجوّع): DEGRADED · UNAVAILABLE (صادق)")


async def test_no_fabrication_without_delta():
    print("\n--- test_no_fabrication_without_delta ---")
    atom, bus = await _mk()
    await atom._on_delta(_delta())
    assert not _out(bus), "بلا دلتا = لا نشر"
    print("OK — بلا دلتا حقيقي: لا فبركة")


async def test_accumulates_delta():
    print("\n--- test_accumulates_delta ---")
    atom, bus = await _mk()
    await atom._on_delta(_delta(10, ratio=0.4))
    await atom._on_delta(_delta(5, ratio=0.2))
    last = _out(bus)[-1]
    assert last["metadata"]["cvd"] == 15, last["metadata"]["cvd"]
    assert last["signal"] == "rising"
    print(f"OK — عند تدفّق: يراكم CVD={last['metadata']['cvd']} → rising")


async def test_confidence_is_measured_not_fixed():
    print("\n--- test_confidence_is_measured_not_fixed ---")
    # §12.3 — أهمّ إصلاح: 1.0 ثابتة كانت لا تميّز دلتا ضعيفة عن قويّة.
    # الآن الثقة = |ratio| المُقاس فعلًا من 256 داخل نفس الحمولة.
    atom, bus = await _mk()
    await atom._on_delta(_delta(10, ratio=0.9))
    strong = _out(bus)[-1]
    await atom._on_delta(_delta(1, ratio=0.05))
    weak = _out(bus)[-1]
    assert strong["confidence"] == 0.9, strong["confidence"]
    assert weak["confidence"] == 0.05, weak["confidence"]
    assert strong["confidence"] != weak["confidence"]
    print(f"OK — ratio=0.9→ثقة {strong['confidence']} · ratio=0.05→ثقة {weak['confidence']} (لا 1.0 ثابتة)")


async def test_no_ratio_no_fabricated_confidence():
    print("\n--- test_no_ratio_no_fabricated_confidence ---")
    atom, bus = await _mk()
    await atom._on_delta(_delta(10))  # بلا ratio — مصدر غير 256
    last = _out(bus)[-1]
    assert last["confidence"] == 0.0, "غياب القياس ⇒ ثقة صفر لا اختلاق 1.0"
    print("OK — بلا ratio: ثقة صفر صادقة لا 1.0 مصطنعة")


async def test_healthy_when_flow_present():
    print("\n--- test_healthy_when_flow_present ---")
    atom, _bus = await _mk()
    await atom._on_delta(_delta(10))
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY
    print("OK — عند وصول الدلتا: HEALTHY")


async def test_lifecycle_before_start():
    print("\n--- test_lifecycle_before_start ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    h = await atom.health_check()
    assert h.state == HealthState.UNHEALTHY
    print("OK — قبل start: UNHEALTHY")


async def main():
    tests = [
        test_unavailable_by_default,
        test_no_fabrication_without_delta,
        test_accumulates_delta,
        test_confidence_is_measured_not_fixed,
        test_no_ratio_no_fabricated_confidence,
        test_healthy_when_flow_present,
        test_lifecycle_before_start,
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
