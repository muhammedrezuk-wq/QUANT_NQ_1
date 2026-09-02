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
    "_atom208", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom208"] = _mod
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
        return AtomContext(atom_id=208, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tr(signal="uptrend", confirms=0, cycle="c1", symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "id": "structure_trend", "cycle_id": cycle, "status": "ok",
            "signal": signal, "score": 0, "confidence": 1.0, "quality": "good",
            "warnings": [], "metadata": {"method": "mss_governed", "timeframe": tf,
                                         "confirmations": confirms, "source": "mss"}}


async def _run(trends):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    for t in trends:
        await atom._on_trend(t)
    ph = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, ph


async def test_zero_confirms_neutral():
    print("\n--- test_zero_confirms_neutral ---")
    _atom, _bus, ph = await _run([_tr("uptrend", confirms=0)])
    assert ph[-1]["signal"] == "neutral", ph[-1]["signal"]
    print("OK — اتجاه بلا تأكيد = neutral")


async def test_one_confirm_early():
    print("\n--- test_one_confirm_early ---")
    _atom, _bus, ph = await _run([_tr("uptrend", confirms=1)])
    assert ph[-1]["signal"] == "early", ph[-1]["signal"]
    print("OK — تأكيد واحد = early")


async def test_mid_confirms_established():
    print("\n--- test_mid_confirms_established ---")
    _atom, _bus, ph = await _run([_tr("downtrend", confirms=3)])
    last = ph[-1]
    assert last["signal"] == "established", last["signal"]
    assert last["score"] > 0
    print(f"OK — 3 تأكيدات = established (score={last['score']})")


async def test_many_confirms_extended():
    print("\n--- test_many_confirms_extended ---")
    _atom, _bus, ph = await _run([_tr("uptrend", confirms=6)])
    last = ph[-1]
    assert last["signal"] == "extended", last["signal"]
    assert last["confidence"] == 1.0
    print("OK — 6 تأكيدات = extended")


async def test_range_is_neutral():
    print("\n--- test_range_is_neutral ---")
    _atom, _bus, ph = await _run([_tr("range", confirms=0), _tr("transition", confirms=0)])
    assert all(p["signal"] == "neutral" for p in ph)
    print("OK — عرضي/انتقالي = neutral")


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
    await atom._on_trend(_tr("uptrend", confirms=1))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_zero_confirms_neutral,
        test_one_confirm_early,
        test_mid_confirms_established,
        test_many_confirms_extended,
        test_range_is_neutral,
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
