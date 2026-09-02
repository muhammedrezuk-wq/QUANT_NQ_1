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
    "_atom254", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom254"] = _mod
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
        return AtomContext(atom_id=254, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _buy(price, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "id": "buyside", "cycle_id": "c", "status": "ok",
            "signal": "buyside", "score": 0, "confidence": 1.0, "quality": "good",
            "warnings": [], "metadata": {"method": "pool_side_filter", "timeframe": tf,
                                         "side": "high", "price": price, "pool_time": 0,
                                         "close": price}}


def _sell(price, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "id": "sellside", "cycle_id": "c", "status": "ok",
            "signal": "sellside", "score": 0, "confidence": 1.0, "quality": "good",
            "warnings": [], "metadata": {"method": "pool_side_filter", "timeframe": tf,
                                         "side": "low", "price": price, "pool_time": 0,
                                         "close": price}}


def _candle(high, low, symbol="NQ100", tf="60s", ps=1.0):
    return {"symbol": symbol, "timeframe": tf, "period_start": ps, "timestamp": ps,
            "open": high, "high": high, "low": low, "close": (high + low) / 2, "volume": 1}


async def _mk():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _sweeps(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_high_sweeps_buyside():
    print("\n--- test_high_sweeps_buyside ---")
    atom, bus = await _mk()
    await atom._on_buyside(_buy(12))
    await atom._on_candle(_candle(high=13, low=11))  # فتيلة فوق 12 → كنس
    last = _sweeps(bus)[-1]
    assert last["signal"] == "sweep", last["signal"]
    assert last["metadata"]["direction"] == "buy_side"
    assert last["metadata"]["price"] == 12
    assert atom._state[("NQ100", "60s")]["buy"] == [], "البركة تُزال بعد الكنس"
    print("OK — فتيلة فوق بركة شراء → كنس buy_side · البركة أُزيلت")


async def test_low_sweeps_sellside():
    print("\n--- test_low_sweeps_sellside ---")
    atom, bus = await _mk()
    await atom._on_sellside(_sell(8))
    await atom._on_candle(_candle(high=10, low=7))  # فتيلة تحت 8 → كنس
    last = _sweeps(bus)[-1]
    assert last["signal"] == "sweep"
    assert last["metadata"]["direction"] == "sell_side"
    assert last["metadata"]["price"] == 8
    print("OK — فتيلة تحت بركة بيع → كنس sell_side")


async def test_no_sweep_within_range():
    print("\n--- test_no_sweep_within_range ---")
    atom, bus = await _mk()
    await atom._on_buyside(_buy(12))
    await atom._on_candle(_candle(high=11, low=10))  # ما وصل 12
    assert _sweeps(bus)[-1]["signal"] == "none"
    print("OK — بلا تجاوز = لا كنس")


async def test_pool_swept_once():
    print("\n--- test_pool_swept_once ---")
    atom, bus = await _mk()
    await atom._on_buyside(_buy(12))
    await atom._on_candle(_candle(high=13, low=11))  # كنس
    await atom._on_candle(_candle(high=14, low=12))  # البركة راحت → لا كنس ثانٍ
    assert atom._sweeps == 1, f"الكنس مرّة وحدة (طلع {atom._sweeps})"
    assert _sweeps(bus)[-1]["signal"] == "none"
    print("OK — البركة تُكنَس مرّة وحدة فقط")


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
    await atom._on_candle(_candle(high=10, low=9))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_high_sweeps_buyside,
        test_low_sweeps_sellside,
        test_no_sweep_within_range,
        test_pool_swept_once,
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
