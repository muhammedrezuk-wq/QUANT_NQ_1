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
    "_atom258", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom258"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"absorption_ratio": 1000}


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
        return AtomContext(atom_id=258, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _candle(high, low, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "timeframe": tf, "period_start": 0.0, "timestamp": 0.0,
            "open": high, "high": high, "low": low, "close": low, "volume": 1}


def _delta(delta=None, symbol="NQ100", tf="60s"):
    meta = {"timeframe": tf}
    if delta is not None:
        meta["delta"] = delta
    # يحاكي حمولة 256 الحقيقية (لم يعد لها score ميت — §12).
    return {"symbol": symbol, "id": "delta", "cycle_id": "c", "status": "ok",
            "signal": "buy_pressure", "confidence": 1.0, "quality": "good",
            "warnings": [], "metadata": meta}


async def _mk():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


def _out(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_unavailable_by_default():
    print("\n--- test_unavailable_by_default ---")
    atom, _bus = await _mk()
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED
    assert h.message == "ORDER_FLOW_UNAVAILABLE"
    print("OK — بلا دلتا: DEGRADED · UNAVAILABLE (صادق)")


async def test_absorbed_high_ratio():
    print("\n--- test_absorbed_high_ratio ---")
    atom, bus = await _mk()
    await atom._on_candle(_candle(10.5, 10.0))   # range = 0.5
    await atom._on_delta(_delta(2000))            # ratio = 4000 >= 1000
    last = _out(bus)[-1]
    assert last["signal"] == "absorbed", last["signal"]
    assert "score" not in last, "لا حقل score ميت — §12"
    print(f"OK — حجم كبير/مدى صغير → absorbed (ratio={last['metadata']['ratio']})")


async def test_normal_low_ratio():
    print("\n--- test_normal_low_ratio ---")
    atom, bus = await _mk()
    await atom._on_candle(_candle(20.0, 10.0))   # range = 10
    await atom._on_delta(_delta(100))             # ratio = 10 < 1000
    assert _out(bus)[-1]["signal"] == "normal"
    print("OK — حركة طبيعية → normal")


async def test_healthy_when_flow_present():
    print("\n--- test_healthy_when_flow_present ---")
    atom, _bus = await _mk()
    await atom._on_candle(_candle(10.5, 10.0))
    await atom._on_delta(_delta(2000))
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY
    print("OK — عند وصول الدلتا: HEALTHY")


async def test_lifecycle_before_start():
    print("\n--- test_lifecycle_before_start ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    h = await atom.health_check()
    assert h.state == HealthState.UNHEALTHY
    print("OK — قبل start: UNHEALTHY")


async def main():
    tests = [
        test_unavailable_by_default,
        test_absorbed_high_ratio,
        test_normal_low_ratio,
        test_healthy_when_flow_present,
        test_lifecycle_before_start,
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
