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
    "_atom152", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom152"] = _mod
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
        return AtomContext(atom_id=152, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"roc_period": 3, "impulse_window": 3, "persistence_window": 4,
       "roc_flat_pct": 0.05, "persistence_min": 0.6,
       "strong_score": 70, "medium_score": 40, "min_candles": 5}


def _candle(close, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "close": close, "timeframe": tf,
            "period_start": 0.0, "timestamp": 0.0}


async def _run(closes, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    for c in closes:
        await atom._on_candle(_candle(c))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, out


async def test_warmup_insufficient():
    print("\n--- test_warmup_insufficient ---")
    _a, _b, out = await _run([100, 101, 102])
    assert out and out[-1]["status"] == "insufficient_data", out[-1]["status"]
    assert out[-1]["signal"] == "sideways"
    print("OK — الإحماء: insufficient_data")


async def test_up_momentum():
    print("\n--- test_up_momentum ---")
    _a, _b, out = await _run([100 + i * 6 for i in range(20)])  # strong steady rise
    last = out[-1]
    assert last["status"] == "ok", last
    assert last["signal"] == "up", last["signal"]
    assert last["score"] > 0
    assert last["level"] in ("medium", "strong")
    assert last["confidence"] > 0.8, last["confidence"]  # persistent up
    assert last["metadata"]["roc"] > 0
    print(f"OK — زخم صاعد: signal={last['signal']} score={last['score']} "
          f"level={last['level']} conf={last['confidence']}")


async def test_down_momentum():
    print("\n--- test_down_momentum ---")
    _a, _b, out = await _run([200 - i * 2 for i in range(20)])
    last = out[-1]
    assert last["signal"] == "down", last["signal"]
    assert last["metadata"]["roc"] < 0
    assert last["confidence"] > 0.8
    print(f"OK — زخم هابط: signal={last['signal']} roc={last['metadata']['roc']}")


async def test_chop_sideways_low_confidence():
    print("\n--- test_chop_sideways_low_confidence ---")
    closes = [100 + (0.5 if i % 2 == 0 else -0.5) for i in range(20)]  # alternating
    _a, _b, out = await _run(closes)
    last = out[-1]
    assert last["signal"] == "sideways", last["signal"]
    assert last["confidence"] < 0.4, f"تذبذب لازم ثقة منخفضة، طلع {last['confidence']}"
    assert last["level"] == "weak"
    print(f"OK — تذبذب: sideways · conf={last['confidence']} (اتساق منخفض)")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _a, _b, out = await _run([100 + i * 2 for i in range(15)])
    last = out[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score", "confidence",
                  "level", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص: {field}"
    for field in ("method", "timeframe", "roc_period", "roc", "impulse", "persistence"):
        assert field in last["metadata"], f"metadata ناقص: {field}"
    assert last["id"] == "momentum"
    assert 0.0 <= last["confidence"] <= 1.0
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(100))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_state_survives_restart():
    print("\n--- test_state_survives_restart ---")
    closes = [100 + i for i in range(10)]
    atom, _bus, out = await _run(closes)
    assert out[-1]["status"] == "ok", out[-1]["status"]
    key = ("NQ100", "60s")
    before = list(atom._closes[key])
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "سلسلة الإغلاقات لازم تُحفظ بجانبه — لا تُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    after = list(revived._closes[key])
    assert after == before, (after, before)
    assert revived._candles_seen == atom._candles_seen

    # شمعة واحدة بعد الإحياء تكمل من حيث وقفت، لا من الصفر.
    await revived._on_candle(_candle(closes[-1] + 1))
    fresh = [p for n, p in bus2.published if n == EVENT_OUT]
    assert fresh and fresh[-1]["status"] != "insufficient_data", fresh[-1]["status"]
    print(f"OK — سلسلة الإغلاقات نجت الإقلاع: count={len(after)} بلا إحماء جديد")


async def main():
    tests = [
        test_warmup_insufficient,
        test_up_momentum,
        test_down_momentum,
        test_chop_sideways_low_confidence,
        test_contract_shape_complete,
        test_health_states,
        test_state_survives_restart,
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
