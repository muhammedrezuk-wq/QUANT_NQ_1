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
    "_atom160", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom160"] = _mod
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
        return AtomContext(atom_id=160, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"anchor_symbol": "USTEC", "window": 30, "corr_threshold": 0.5}

_ANCHOR = [100, 110] * 6           # alternating -> varying returns
_X_POS = [200, 220] * 6            # same alternation -> r ~ +1
_X_NEG = [220, 200] * 6            # inverted -> r ~ -1
_X_WEAK = [100 + i for i in range(12)]  # steady -> ~0 corr with alternation


def _candle(symbol, close, period, tf="60s"):
    return {"symbol": symbol, "close": close, "timeframe": tf, "period_start": period}


async def _corr(anchor_closes, x_closes, x_symbol="EURUSD"):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    for i, c in enumerate(anchor_closes):
        await atom._on_candle(_candle("USTEC", c, i + 1))
    for i, c in enumerate(x_closes):
        await atom._on_candle(_candle(x_symbol, c, i + 1))
    xs = [p for n, p in bus.published
          if n == EVENT_OUT and p["symbol"] == x_symbol and p["status"] == "ok"]
    return xs[-1]


async def test_positive():
    print("\n--- test_positive ---")
    o = await _corr(_ANCHOR, _X_POS)
    assert o["signal"] == "positive", (o["signal"], o["metadata"]["correlation"])
    print(f"OK — موجب: r={o['metadata']['correlation']}")


async def test_negative():
    print("\n--- test_negative ---")
    o = await _corr(_ANCHOR, _X_NEG)
    assert o["signal"] == "negative", (o["signal"], o["metadata"]["correlation"])
    print(f"OK — سالب: r={o['metadata']['correlation']}")


async def test_weak():
    print("\n--- test_weak ---")
    o = await _corr(_ANCHOR, _X_WEAK)
    assert o["signal"] == "weak", (o["signal"], o["metadata"]["correlation"])
    print(f"OK — ضعيف: r={o['metadata']['correlation']}")


async def test_anchor():
    print("\n--- test_anchor ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    for i, c in enumerate(_ANCHOR):
        await atom._on_candle(_candle("USTEC", c, i + 1))
    o = [p for n, p in bus.published
         if n == EVENT_OUT and p["symbol"] == "USTEC" and p["status"] == "ok"][-1]
    assert o["signal"] == "anchor", o["signal"]
    print("OK — المرساة: signal=anchor r=1")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    o = await _corr(_ANCHOR, _X_POS)
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in o, f"حقل ناقص: {f}"
    for f in ("method", "anchor", "correlation", "points"):
        assert f in o["metadata"], f"metadata ناقص: {f}"
    assert o["id"] == "correlation"
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle("USTEC", 100, 1))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_state_survives_restart():
    print("\n--- test_state_survives_restart ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    for i, c in enumerate(_ANCHOR):
        await atom._on_candle(_candle("USTEC", c, i + 1))
    for i, c in enumerate(_X_POS):
        await atom._on_candle(_candle("EURUSD", c, i + 1))
    anchor_key = ("USTEC", "60s")
    x_key = ("EURUSD", "60s")
    before_anchor = list(atom._state[anchor_key]["rets"])
    before_x = list(atom._state[x_key]["rets"])
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" in saved, "تاريخ العوائد لازم يُحفظ بجانبه — لا يُدهَس"

    revived = Atom()
    bus2 = FakeEventBus()
    await revived.initialize(bus2.make_context(dict(CFG)))
    await revived.restore(saved)
    await revived.start()
    assert list(revived._state[anchor_key]["rets"]) == before_anchor
    assert list(revived._state[x_key]["rets"]) == before_x
    assert revived._candles_seen == atom._candles_seen

    await revived._on_candle(_candle("EURUSD", 220, len(_X_POS) + 1))
    fresh = [p for n, p in bus2.published
             if n == EVENT_OUT and p["symbol"] == "EURUSD"]
    assert fresh and fresh[-1]["status"] == "ok", fresh[-1]["status"]
    print(f"OK — تاريخ العوائد نجا الإقلاع: "
          f"r={fresh[-1]['metadata']['correlation']} بلا إحماء جديد")


async def main():
    tests = [test_positive, test_negative, test_weak, test_anchor,
             test_contract_shape, test_health_states, test_state_survives_restart]
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
