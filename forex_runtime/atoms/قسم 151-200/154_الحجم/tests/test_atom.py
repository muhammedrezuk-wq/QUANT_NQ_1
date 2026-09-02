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
    "_atom154", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom154"] = _mod
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
        return AtomContext(atom_id=154, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"baseline_window": 10, "trend_short": 3, "trend_long": 6,
       "high_mult": 1.5, "spike_mult": 2.5, "min_candles": 6}


def _candle(vol, close=100.0, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "volume": vol, "close": close, "timeframe": tf,
            "period_start": 0.0, "timestamp": 0.0}


async def _run(pairs, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    for v, c in pairs:
        await atom._on_candle(_candle(v, c))
    return atom, bus, [p for n, p in bus.published if n == EVENT_OUT]


async def test_warmup_insufficient():
    print("\n--- test_warmup_insufficient ---")
    _a, _b, out = await _run([(100.0, 100.0)] * 3)
    assert out and out[-1]["status"] == "insufficient_data"
    print("OK — الإحماء: insufficient_data")


async def test_accumulation():
    print("\n--- test_accumulation ---")
    _a, _b, out = await _run([(100.0, 100.0)] * 10 + [(300.0, 101.0)])  # حجم عالي + سعر صاعد
    last = out[-1]
    assert last["status"] == "ok" and last["signal"] == "accumulation", last
    assert last["metadata"]["ratio"] >= 1.5
    print(f"OK — تجميع: signal={last['signal']} ratio={last['metadata']['ratio']} spike={last['metadata']['spike']}")


async def test_distribution():
    print("\n--- test_distribution ---")
    _a, _b, out = await _run([(100.0, 100.0)] * 10 + [(300.0, 99.0)])  # حجم عالي + سعر هابط
    last = out[-1]
    assert last["signal"] == "distribution", last["signal"]
    print(f"OK — تصريف: signal={last['signal']} ratio={last['metadata']['ratio']}")


async def test_normal():
    print("\n--- test_normal ---")
    _a, _b, out = await _run([(100.0, 100.0)] * 11)  # حجم عادي
    last = out[-1]
    assert last["signal"] == "normal", last["signal"]
    print(f"OK — عادي: signal={last['signal']} ratio={last['metadata']['ratio']}")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    _a, _b, out = await _run([(100.0, 100.0)] * 10 + [(150.0, 101.0)])
    last = out[-1]
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in last, f"حقل ناقص: {f}"
    for f in ("method", "source", "volume", "avg_volume", "ratio", "spike", "volume_trend"):
        assert f in last["metadata"], f"metadata ناقص: {f}"
    assert last["id"] == "volume" and last["metadata"]["source"] == "tick"
    print("OK — العقد الموحّد كامل + قياسات الحجم")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_state_survives_restart():
    print("\n--- test_state_survives_restart ---")
    pairs = [(100.0, 100.0)] * 8
    atom, _bus, out = await _run(pairs)
    assert out[-1]["status"] == "ok", out[-1]["status"]
    key = ("NQ100", "60s")
    before = dict(atom._state[key])
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "سلسلة الأحجام لازم تُحفظ بجانبه — لا تُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    after = revived._state[key]
    assert list(after["vol"]) == list(before["vol"])
    assert after["prev_close"] == before["prev_close"]
    assert revived._candles_seen == atom._candles_seen

    await revived._on_candle(_candle(100.0, 100.0))
    fresh = [p for n, p in bus2.published if n == EVENT_OUT]
    assert fresh and fresh[-1]["status"] != "insufficient_data", fresh[-1]["status"]
    print(f"OK — تاريخ الحجم نجا الإقلاع: vol={len(after['vol'])} بلا إحماء جديد")


async def main():
    tests = [test_warmup_insufficient, test_accumulation, test_distribution,
             test_normal, test_contract_shape, test_health_states,
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
