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
    "_atom156", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom156"] = _mod
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
        return AtomContext(atom_id=156, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"doji_body_ratio": 0.1, "marubozu_body_ratio": 0.85,
       "pin_wick_ratio": 0.6, "pin_body_ratio": 0.3}


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


# مرجع يشارك نفس القمة/القاع (98..102) وبنفس اللون → لا inside/outside/engulfing
_SPAN = _candle(98.0, 102.0, 98.0, 102.0)


async def test_warmup_no_prev():
    print("\n--- test_warmup_no_prev ---")
    out = await _run([_candle(99.0, 101.0, 98.0, 100.0)])
    assert out and out[-1]["status"] == "insufficient_data"
    print("OK — أول شمعة بلا سابقة: insufficient_data")


async def test_doji():
    print("\n--- test_doji ---")
    out = await _run([_SPAN, _candle(100.0, 102.0, 98.0, 100.05)])
    assert out[-1]["signal"] == "doji", out[-1]["signal"]
    print("OK — doji")


async def test_marubozu():
    print("\n--- test_marubozu ---")
    out = await _run([_SPAN, _candle(98.1, 102.0, 98.0, 101.9)])
    assert out[-1]["signal"] == "marubozu", out[-1]["signal"]
    print("OK — marubozu")


async def test_pin_bar():
    print("\n--- test_pin_bar ---")
    out = await _run([_SPAN, _candle(101.5, 102.0, 98.0, 101.6)])  # جسم صغير فوق + ذيل سفلي طويل
    assert out[-1]["signal"] == "pin_bar", out[-1]["signal"]
    print("OK — pin_bar")


async def test_engulfing():
    print("\n--- test_engulfing ---")
    prev = _candle(100.3, 100.5, 100.0, 100.1)   # هابطة صغيرة
    cur = _candle(99.0, 101.0, 98.5, 101.0)       # صاعدة تبتلعها
    out = await _run([prev, cur])
    assert out[-1]["signal"] == "engulfing", out[-1]["signal"]
    assert out[-1]["metadata"]["direction"] == "bullish"
    print("OK — engulfing (bullish)")


async def test_inside():
    print("\n--- test_inside ---")
    out = await _run([_SPAN, _candle(100.0, 101.0, 99.0, 100.5)])
    assert out[-1]["signal"] == "inside", out[-1]["signal"]
    print("OK — inside")


async def test_outside():
    print("\n--- test_outside ---")
    prev = _candle(100.0, 100.5, 99.5, 100.2)     # صغيرة صاعدة
    cur = _candle(99.0, 102.0, 98.0, 101.0)        # صاعدة تحيط بها (نفس اللون → مش engulfing)
    out = await _run([prev, cur])
    assert out[-1]["signal"] == "outside", out[-1]["signal"]
    print("OK — outside")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    out = await _run([_SPAN, _candle(98.1, 102.0, 98.0, 101.9)])
    last = out[-1]
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in last, f"حقل ناقص: {f}"
    for f in ("method", "pattern", "direction", "body_ratio", "upper_wick", "lower_wick"):
        assert f in last["metadata"], f"metadata ناقص: {f}"
    assert last["id"] == "candle"
    print("OK — العقد الموحّد كامل + هندسة الشمعة")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(99.0, 101.0, 98.0, 100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_prev_candle_survives_restart():
    print("\n--- test_prev_candle_survives_restart ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle(_SPAN)
    await atom._on_candle(_candle(98.1, 102.0, 98.0, 101.9))
    key = ("NQ100", "60s")
    before = dict(atom._prev[key])
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "الشمعة السابقة لازم تُحفظ بجانبه — لا تُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    after = revived._prev[key]
    assert after == before, (after, before)
    assert revived._candles_seen == atom._candles_seen

    # شمعة واحدة بعد الإحياء تُقارَن بالسابقة المستعادة، لا insufficient_data.
    await revived._on_candle(_candle(100.0, 102.0, 98.0, 100.05))
    fresh = [p for n, p in bus2.published if n == EVENT_OUT]
    assert fresh and fresh[-1]["status"] != "insufficient_data", fresh[-1]["status"]
    print(f"OK — الشمعة السابقة نجت الإقلاع: open={after['open']} بلا insufficient_data")


async def main():
    tests = [test_warmup_no_prev, test_doji, test_marubozu, test_pin_bar,
             test_engulfing, test_inside, test_outside, test_contract_shape,
             test_health_states, test_prev_candle_survives_restart]
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
