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
    "_atom157", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom157"] = _mod
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
        return AtomContext(atom_id=157, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"gap_threshold_pct": 0.1}


def _candle(o, h, low, c, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "open": o, "high": h, "low": low, "close": c,
            "timeframe": tf, "period_start": 0.0, "timestamp": 0.0}


async def _run(candles):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    for cd in candles:
        await atom._on_candle(cd)
    return [p for n, p in bus.published if n == EVENT_OUT]


_PREV = _candle(99.0, 100.0, 99.0, 100.0)  # إغلاق 100


async def test_warmup_no_prev():
    print("\n--- test_warmup_no_prev ---")
    out = await _run([_candle(100.0, 100.5, 99.5, 100.0)])
    assert out and out[-1]["status"] == "insufficient_data"
    print("OK — بلا سابقة: insufficient_data")


async def test_gap_up():
    print("\n--- test_gap_up ---")
    out = await _run([_PREV, _candle(101.0, 101.5, 100.8, 101.2)])  # فتح فوق الإغلاق 100
    last = out[-1]
    assert last["status"] == "ok" and last["signal"] == "gap_up", last
    assert last["metadata"]["gap_pct"] >= 0.1
    print(f"OK — gap_up: pct={last['metadata']['gap_pct']} score={last['score']}")


async def test_gap_down():
    print("\n--- test_gap_down ---")
    out = await _run([_PREV, _candle(99.0, 99.4, 98.5, 99.1)])
    assert out[-1]["signal"] == "gap_down", out[-1]["signal"]
    print(f"OK — gap_down: pct={out[-1]['metadata']['gap_pct']}")


async def test_no_gap():
    print("\n--- test_no_gap ---")
    out = await _run([_PREV, _candle(100.02, 100.3, 99.8, 100.1)])  # فرق ضئيل
    assert out[-1]["signal"] == "none", out[-1]["signal"]
    print("OK — بلا فجوة: none")


async def test_gap_filled():
    print("\n--- test_gap_filled ---")
    # فجوة صعود (level=100)، ثم شمعة تعود وتلمس 100 → filled
    out = await _run([_PREV,
                      _candle(101.0, 101.5, 100.8, 101.2),      # gap_up، level=100
                      _candle(101.2, 101.3, 99.5, 100.5)])       # تمتدّ عبر 100 → تملأها
    last = out[-1]
    assert last["signal"] == "filled", last["signal"]
    assert last["metadata"]["filled"] is True
    print(f"OK — filled: age={last['metadata']['open_gap_age']}")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    out = await _run([_PREV, _candle(101.0, 101.5, 100.8, 101.2)])
    last = out[-1]
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in last, f"حقل ناقص: {f}"
    for f in ("method", "gap_pct", "gap_size", "gap_type", "filled", "open_gap_age"):
        assert f in last["metadata"], f"metadata ناقص: {f}"
    assert last["id"] == "gap"
    print("OK — العقد الموحّد كامل + Size/Type/Filled/Age")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(100.0, 100.5, 99.5, 100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_open_gap_survives_restart():
    print("\n--- test_open_gap_survives_restart ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle(_PREV)                                # prev_close=100
    await atom._on_candle(_candle(101.0, 101.5, 100.8, 101.2))   # gap_up, level=100
    key = ("NQ100", "60s")
    before = dict(atom._state[key])
    assert before["open_gap"] is not None, "الاختبار يفترض فجوة مفتوحة قبل اللقطة"
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "الفجوة المفتوحة لازم تُحفظ بجانبه — لا تُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    after = revived._state[key]
    assert after["open_gap"] == before["open_gap"], (after["open_gap"], before["open_gap"])
    assert after["prev_close"] == before["prev_close"]
    assert revived._candles_seen == atom._candles_seen

    # شمعة تلمس مستوى الفجوة بعد الاستعادة لازم تُعلنها filled — لا فجوة
    # صفرية جديدة كأن شيئًا لم يكن.
    await revived._on_candle(_candle(101.2, 101.3, 99.5, 100.5))
    fresh = [p for n, p in bus2.published if n == EVENT_OUT]
    assert fresh and fresh[-1]["signal"] == "filled", fresh[-1]["signal"]
    print(f"OK — الفجوة المفتوحة نجت الإقلاع: {after['open_gap']} → filled بعد الاستعادة")


async def main():
    tests = [test_warmup_no_prev, test_gap_up, test_gap_down, test_no_gap,
             test_gap_filled, test_contract_shape, test_health_states,
             test_open_gap_survives_restart]
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
