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
    "_atom203", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom203"] = _mod
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
        return AtomContext(atom_id=203, config=config, logger=_NullLogger(),
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
    internal = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, internal


async def test_no_structure_insufficient():
    print("\n--- test_no_structure_insufficient ---")
    _atom, _bus, ins = await _run([_swing("none"), _swing("none")])
    last = ins[-1]
    assert last["status"] == "insufficient_data", last["status"]
    assert last["signal"] == "none"
    print("OK — بلا بنية بعد: insufficient_data")


async def test_lower_high_pullback():
    print("\n--- test_lower_high_pullback ---")
    _atom, _bus, ins = await _run([
        _swing("swing_high", 12, ts=1, score=50),
        _swing("swing_high", 10, ts=2, score=45)])
    last = ins[-1]
    assert last["status"] == "ok", last
    assert last["signal"] == "LH", last["signal"]
    assert last["metadata"]["swing_high"] == 10
    assert last["confidence"] == 1.0
    print(f"OK — قمة أدنى (ارتداد هابط): LH · مستوى={last['metadata']['swing_high']}")


async def test_higher_low_pullback():
    print("\n--- test_higher_low_pullback ---")
    _atom, _bus, ins = await _run([
        _swing("swing_low", 8, ts=1, score=40),
        _swing("swing_low", 10, ts=2, score=48)])
    last = ins[-1]
    assert last["signal"] == "HL", last["signal"]
    assert last["metadata"]["swing_low"] == 10
    print(f"OK — قاع أعلى (ارتداد صاعد): HL · مستوى={last['metadata']['swing_low']}")


async def test_first_swing_no_event():
    print("\n--- test_first_swing_no_event ---")
    _atom, _bus, ins = await _run([_swing("swing_high", 12, ts=1, score=50)])
    last = ins[-1]
    assert last["status"] == "ok", last
    assert last["signal"] == "none", "أول قمة بلا سابقة = لا حدث LH"
    print("OK — أول قمة: بلا حدث (بحقّ)")


async def test_extension_is_not_internal():
    print("\n--- test_extension_is_not_internal ---")
    # قمة أعلى = امتداد (شغل 202) → 203 لا يطلّع حدثًا داخليًّا
    _atom, _bus, ins = await _run([
        _swing("swing_high", 10, ts=1, score=50),
        _swing("swing_high", 12, ts=2, score=60)])
    last = ins[-1]
    assert last["signal"] == "none", "الامتداد (HH) مو داخلي — شغل 202"
    print("OK — الامتداد (قمة أعلى) مو داخلي")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _atom, _bus, ins = await _run([
        _swing("swing_high", 12, ts=1), _swing("swing_high", 10, ts=2)])
    last = ins[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص بالعقد: {field}"
    for field in ("method", "timeframe", "swing_high", "swing_high_time",
                  "swing_low", "swing_low_time", "close"):
        assert field in last["metadata"], f"حقل metadata ناقص: {field}"
    assert last["id"] == "internal"
    print("OK — العقد الموحّد كامل الحقول")


async def main():
    tests = [
        test_no_structure_insufficient,
        test_lower_high_pullback,
        test_higher_low_pullback,
        test_first_swing_no_event,
        test_extension_is_not_internal,
        test_contract_shape_complete,
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
