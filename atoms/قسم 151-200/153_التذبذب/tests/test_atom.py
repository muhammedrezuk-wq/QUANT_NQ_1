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
    "_atom153", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom153"] = _mod
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
        return AtomContext(atom_id=153, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"atr_window": 3, "baseline_window": 12, "stddev_window": 5,
       "high_mult": 1.5, "low_mult": 0.6, "min_candles": 8}


def _candle(rng, close=100.0, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "high": close + rng / 2.0, "low": close - rng / 2.0,
            "close": close, "timeframe": tf, "period_start": 0.0, "timestamp": 0.0}


async def _run(ranges, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    for r in ranges:
        await atom._on_candle(_candle(r))
    return atom, bus, [p for n, p in bus.published if n == EVENT_OUT]


async def test_warmup_insufficient():
    print("\n--- test_warmup_insufficient ---")
    _a, _b, out = await _run([1.0, 1.0, 1.0])
    assert out and out[-1]["status"] == "insufficient_data"
    print("OK — الإحماء: insufficient_data")


async def test_high_volatility():
    print("\n--- test_high_volatility ---")
    _a, _b, out = await _run([1.0] * 12 + [5.0] * 3)  # هدوء ثم اتساع
    last = out[-1]
    assert last["status"] == "ok" and last["signal"] == "high", last
    assert last["metadata"]["ratio"] > 1.5
    print(f"OK — تذبذب عالي: signal={last['signal']} ratio={last['metadata']['ratio']} score={last['score']}")


async def test_low_volatility():
    print("\n--- test_low_volatility ---")
    _a, _b, out = await _run([5.0] * 12 + [0.5] * 3)  # اتساع ثم هدوء
    last = out[-1]
    assert last["signal"] == "low", last["signal"]
    assert last["metadata"]["ratio"] < 0.6
    print(f"OK — تذبذب منخفض: signal={last['signal']} ratio={last['metadata']['ratio']}")


async def test_normal_volatility():
    print("\n--- test_normal_volatility ---")
    _a, _b, out = await _run([2.0] * 15)  # ثابت
    last = out[-1]
    assert last["signal"] == "normal", last["signal"]
    print(f"OK — تذبذب طبيعي: signal={last['signal']} ratio={last['metadata']['ratio']}")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    _a, _b, out = await _run([2.0] * 12 + [3.0] * 3)
    last = out[-1]
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in last, f"حقل ناقص: {f}"
    for f in ("method", "atr", "atr_pct", "baseline_atr", "ratio", "range", "stddev"):
        assert f in last["metadata"], f"metadata ناقص: {f}"
    assert last["id"] == "volatility"
    print("OK — العقد الموحّد كامل + قياسات ATR/StdDev/Range")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(1.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_state_survives_restart():
    print("\n--- test_state_survives_restart ---")
    ranges = [1.0, 1.2, 0.8, 1.5, 1.0, 1.3, 0.9, 1.1, 1.4, 1.0]
    atom, _bus, out = await _run(ranges)
    assert out[-1]["status"] == "ok", out[-1]["status"]
    key = ("NQ100", "60s")
    before = dict(atom._state[key])
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "المدى الحقيقي وسلسلة الإغلاقات لازم تُحفظ بجانبه — لا تُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    after = revived._state[key]
    assert list(after["tr"]) == list(before["tr"])
    assert list(after["closes"]) == list(before["closes"])
    assert after["prev_close"] == before["prev_close"]
    assert revived._candles_seen == atom._candles_seen

    await revived._on_candle(_candle(1.0))
    fresh = [p for n, p in bus2.published if n == EVENT_OUT]
    assert fresh and fresh[-1]["status"] != "insufficient_data", fresh[-1]["status"]
    print(f"OK — تاريخ التذبذب نجا الإقلاع: tr={len(after['tr'])} بلا إحماء جديد")


async def main():
    tests = [test_warmup_insufficient, test_high_volatility, test_low_volatility,
             test_normal_volatility, test_contract_shape, test_health_states,
             test_state_survives_restart]
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
