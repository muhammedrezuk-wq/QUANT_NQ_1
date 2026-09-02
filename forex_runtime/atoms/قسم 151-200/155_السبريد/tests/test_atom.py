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
    "_atom155", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom155"] = _mod
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
        return AtomContext(atom_id=155, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"baseline_window": 12, "exp_short": 3, "exp_long": 6, "exp_mult": 1.3,
       "wide_mult": 1.5, "narrow_mult": 0.6, "min_candles": 6}


def _candle(rng, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "high": 100.0 + rng, "low": 100.0,
            "timeframe": tf, "period_start": 0.0, "timestamp": 0.0}


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
    _a, _b, out = await _run([2.0, 2.0, 2.0])
    assert out and out[-1]["status"] == "insufficient_data"
    print("OK — الإحماء: insufficient_data")


async def test_expansion():
    print("\n--- test_expansion ---")
    _a, _b, out = await _run([1.0] * 6 + [3.0, 4.0, 5.0])  # مدى يتوسّع
    last = out[-1]
    assert last["status"] == "ok" and last["signal"] == "expansion", last
    assert last["metadata"]["expansion_ratio"] > 1.3
    print(f"OK — توسّع: signal={last['signal']} ratio={last['metadata']['expansion_ratio']}")


async def test_contraction():
    print("\n--- test_contraction ---")
    _a, _b, out = await _run([5.0] * 6 + [3.0, 2.0, 1.0])  # مدى ينكمش
    last = out[-1]
    assert last["signal"] == "contraction", last["signal"]
    print(f"OK — انكماش: signal={last['signal']} ratio={last['metadata']['expansion_ratio']}")


async def test_stable():
    print("\n--- test_stable ---")
    _a, _b, out = await _run([2.0] * 9)
    last = out[-1]
    assert last["signal"] == "stable", last["signal"]
    print(f"OK — مستقرّ: signal={last['signal']} ratio={last['metadata']['expansion_ratio']}")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    _a, _b, out = await _run([1.0] * 6 + [2.0, 3.0, 4.0])
    last = out[-1]
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in last, f"حقل ناقص: {f}"
    for f in ("method", "range", "baseline_range", "expansion_ratio", "size"):
        assert f in last["metadata"], f"metadata ناقص: {f}"
    assert last["id"] == "spread"
    print("OK — العقد الموحّد كامل + توسّع/انكماش + wide/narrow")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(2.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_state_survives_restart():
    print("\n--- test_state_survives_restart ---")
    ranges = [2.0] * 8
    atom, _bus, out = await _run(ranges)
    assert out[-1]["status"] == "ok", out[-1]["status"]
    key = ("NQ100", "60s")
    before = list(atom._ranges[key])
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "سلسلة المديات لازم تُحفظ بجانبه — لا تُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    after = list(revived._ranges[key])
    assert after == before, (after, before)
    assert revived._candles_seen == atom._candles_seen

    await revived._on_candle(_candle(2.0))
    fresh = [p for n, p in bus2.published if n == EVENT_OUT]
    assert fresh and fresh[-1]["status"] != "insufficient_data", fresh[-1]["status"]
    print(f"OK — تاريخ المديات نجا الإقلاع: ranges={len(after)} بلا إحماء جديد")


async def main():
    tests = [test_warmup_insufficient, test_expansion, test_contraction,
             test_stable, test_contract_shape, test_health_states,
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
