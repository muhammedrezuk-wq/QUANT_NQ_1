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
    "_atom253", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom253"] = _mod
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
        return AtomContext(atom_id=253, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _pool(signal="none", price=None, side=None, close=100.0, pt=0.0, score=0,
          symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "id": "pool", "cycle_id": "%s|%s|%s" % (symbol, tf, pt),
            "status": "ok", "signal": signal, "score": score,
            "confidence": 1.0 if signal != "none" else 0.0, "quality": "good",
            "warnings": [], "metadata": {"method": "swing_as_pool", "timeframe": tf,
                                         "side": side, "price": price, "pool_time": pt,
                                         "close": close}}


async def _run(pools):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    for p in pools:
        await atom._on_pool(p)
    out = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, out


async def test_pool_low_becomes_sellside():
    print("\n--- test_pool_low_becomes_sellside ---")
    _atom, _bus, out = await _run([_pool("pool_low", 8, "low", pt=2, score=55)])
    last = out[-1]
    assert last["signal"] == "sellside", last["signal"]
    assert last["metadata"]["price"] == 8 and last["metadata"]["side"] == "low"
    assert last["confidence"] == 0.55, last["confidence"]
    print(f"OK — بركة قاع → سيولة بيع: price={last['metadata']['price']} "
          f"confidence={last['confidence']}")


async def test_confidence_scales_with_pool_score():
    print("\n--- test_confidence_scales_with_pool_score ---")
    # §12.3 — الثقة لم تعد ثنائية: بركة بروزها 20 غير بركة بروزها 90.
    _atom, _bus, weak = await _run([_pool("pool_low", 8, "low", pt=2, score=20)])
    _atom, _bus, strong = await _run([_pool("pool_low", 8, "low", pt=2, score=90)])
    assert weak[-1]["confidence"] == 0.2, weak[-1]["confidence"]
    assert strong[-1]["confidence"] == 0.9, strong[-1]["confidence"]
    assert weak[-1]["confidence"] != strong[-1]["confidence"]
    print(f"OK — بروز 20→ثقة {weak[-1]['confidence']} · بروز 90→ثقة {strong[-1]['confidence']}")


async def test_pool_high_ignored():
    print("\n--- test_pool_high_ignored ---")
    _atom, _bus, out = await _run([_pool("pool_high", 12, "high", pt=2)])
    assert out[-1]["signal"] == "none", "بركة قمّة ليست سيولة بيع"
    print("OK — بركة قمّة تُستبعَد (شغل 252)")


async def test_none_ignored():
    print("\n--- test_none_ignored ---")
    _atom, _bus, out = await _run([_pool("none")])
    assert out[-1]["signal"] == "none"
    print("OK — لا بركة = none")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _atom, _bus, out = await _run([_pool("pool_low", 8, "low", pt=2, score=55)])
    last = out[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص: {field}"
    for field in ("method", "timeframe", "side", "price", "pool_time", "close"):
        assert field in last["metadata"], f"حقل metadata ناقص: {field}"
    assert last["id"] == "sellside"
    print("OK — العقد الموحّد كامل")


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
    await atom._on_pool(_pool("pool_low", 8, "low", pt=1))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_pool_low_becomes_sellside,
        test_confidence_scales_with_pool_score,
        test_pool_high_ignored,
        test_none_ignored,
        test_contract_shape_complete,
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
