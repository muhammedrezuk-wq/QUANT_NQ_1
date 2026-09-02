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
    "_atom255", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom255"] = _mod
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
        return AtomContext(atom_id=255, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _candle(high, low, ps=0.0, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "timeframe": tf, "period_start": ps, "timestamp": ps,
            "open": (high + low) / 2, "high": high, "low": low,
            "close": (high + low) / 2, "volume": 1}


async def _run(bars):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    for i, (h, l) in enumerate(bars):
        await atom._on_candle(_candle(h, l, ps=float(i)))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, out


async def test_warmup_insufficient():
    print("\n--- test_warmup_insufficient ---")
    _atom, _bus, out = await _run([(10, 9), (11, 10)])  # < 3
    last = out[-1]
    assert last["status"] == "insufficient_data"
    assert last["signal"] == "none"
    assert "score" not in last, "لا حقل score ميت — §12"
    print("OK — الإحماء (<3 شموع): insufficient")


async def test_bullish_fvg():
    print("\n--- test_bullish_fvg ---")
    # candle1.high(10) < candle3.low(12) → فجوة صعودية
    _atom, _bus, out = await _run([(10, 9), (15, 11), (16, 12)])
    last = out[-1]
    assert last["signal"] == "fvg_bullish", last["signal"]
    assert last["metadata"]["gap_bottom"] == 10 and last["metadata"]["gap_top"] == 12
    assert "score" not in last, "لا حقل score ميت — §12"
    # الثقة = حجم الفجوة (2) ÷ مدى النافذة الثلاثيّة (16-9=7) = 0.2857
    assert last["confidence"] == 0.2857, last["confidence"]
    print(f"OK — فجوة صعودية: [{last['metadata']['gap_bottom']}, {last['metadata']['gap_top']}] "
          f"confidence={last['confidence']}")


async def test_bearish_fvg():
    print("\n--- test_bearish_fvg ---")
    # candle1.low(15) > candle3.high(13) → فجوة هبوطية
    _atom, _bus, out = await _run([(16, 15), (14, 10), (13, 12)])
    last = out[-1]
    assert last["signal"] == "fvg_bearish", last["signal"]
    assert last["metadata"]["gap_top"] == 15 and last["metadata"]["gap_bottom"] == 13
    # الثقة = حجم الفجوة (2) ÷ مدى النافذة الثلاثيّة (16-10=6) = 0.3333
    assert last["confidence"] == 0.3333, last["confidence"]
    print(f"OK — فجوة هبوطية: [{last['metadata']['gap_bottom']}, {last['metadata']['gap_top']}] "
          f"confidence={last['confidence']}")


async def test_confidence_scales_with_gap_size():
    print("\n--- test_confidence_scales_with_gap_size ---")
    # §12.3 — الثقة لم تعد ثنائية 1.0/0.0: فجوة تملأ معظم مدى النافذة
    # أوثق من فجوة تسدّ جزءًا صغيرًا منه.
    _atom, _bus, wide = await _run([(10, 9), (30, 20), (40, 39)])  # فجوة كبيرة نسبيًّا
    _atom, _bus, narrow = await _run([(10, 9), (100, 5), (11, 10.5)])  # فجوة ضيّقة داخل مدى شاسع
    assert wide[-1]["signal"] == "fvg_bullish" and narrow[-1]["signal"] == "fvg_bullish"
    assert wide[-1]["confidence"] != narrow[-1]["confidence"]
    assert 0.0 < narrow[-1]["confidence"] < wide[-1]["confidence"] <= 1.0
    print(f"OK — فجوة ضيّقة→ثقة {narrow[-1]['confidence']} · فجوة واسعة→ثقة {wide[-1]['confidence']}")


async def test_overlap_no_fvg():
    print("\n--- test_overlap_no_fvg ---")
    _atom, _bus, out = await _run([(10, 8), (11, 9), (12, 10)])  # متداخلة
    assert out[-1]["signal"] == "none", "شموع متداخلة = لا فجوة"
    print("OK — شموع متداخلة: لا فجوة")


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
    await atom._on_candle(_candle(10, 9))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_warmup_insufficient,
        test_bullish_fvg,
        test_bearish_fvg,
        test_confidence_scales_with_gap_size,
        test_overlap_no_fvg,
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
