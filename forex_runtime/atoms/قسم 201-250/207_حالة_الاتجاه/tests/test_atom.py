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
    "_atom207", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom207"] = _mod
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
        return AtomContext(atom_id=207, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _mss(signal="none", shift_type=None, direction=None, cycle="c1", symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "id": "mss", "cycle_id": cycle, "status": "ok",
            "signal": signal, "score": 0, "confidence": 1.0 if signal == "shift" else 0.0,
            "quality": "good", "warnings": [],
            "metadata": {"method": "shift_unifier", "timeframe": tf,
                         "shift_type": shift_type, "direction": direction, "level": None}}


def _t151(signal="sideways", symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "id": "trend", "cycle_id": "x", "status": "ok",
            "signal": signal, "score": 50, "confidence": 0.7, "strength": "moderate",
            "phase": "established", "quality": "good", "warnings": [],
            "metadata": {"method": "ema_slope", "timeframe": tf}}


async def _mk():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _trend(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_default_from_151_before_mss():
    print("\n--- test_default_from_151_before_mss ---")
    atom, bus = await _mk()
    await atom._on_trend151(_t151("up"))
    await atom._on_mss(_mss(signal="none"))
    last = _trend(bus)[-1]
    assert last["signal"] == "uptrend", last["signal"]
    assert last["metadata"]["source"] == "trend_151"
    assert last["confidence"] == 0.5
    print("OK — قبل MSS: 151 يحدّد الافتراضي (uptrend · tentative)")


async def test_bos_up_sets_uptrend():
    print("\n--- test_bos_up_sets_uptrend ---")
    atom, bus = await _mk()
    await atom._on_mss(_mss(signal="shift", shift_type="bos", direction="up"))
    last = _trend(bus)[-1]
    assert last["signal"] == "uptrend"
    assert last["metadata"]["source"] == "mss"
    assert last["confidence"] == 1.0
    assert last["metadata"]["confirmations"] == 1
    print("OK — BOS صاعد → uptrend مؤكَّد (confirmations=1)")


async def test_choch_sets_transition():
    print("\n--- test_choch_sets_transition ---")
    atom, bus = await _mk()
    await atom._on_mss(_mss(signal="shift", shift_type="bos", direction="up"))
    await atom._on_mss(_mss(signal="shift", shift_type="choch", direction="down"))
    last = _trend(bus)[-1]
    assert last["signal"] == "transition", last["signal"]
    assert last["confidence"] == 0.5
    print("OK — CHoCH → transition")


async def test_mss_governs_over_151():
    print("\n--- test_mss_governs_over_151 ---")
    atom, bus = await _mk()
    await atom._on_mss(_mss(signal="shift", shift_type="bos", direction="up"))  # has_mss
    await atom._on_trend151(_t151("down"))   # يجب ألا يتجاوز MSS
    await atom._on_mss(_mss(signal="none"))
    last = _trend(bus)[-1]
    assert last["signal"] == "uptrend", "MSS يحكم بعد وصوله — 151 لا يتجاوزه"
    print("OK — MSS الحاكم: 151 المعاكس لا يتجاوزه")


async def test_confirmations_grow():
    print("\n--- test_confirmations_grow ---")
    atom, bus = await _mk()
    for _ in range(3):
        await atom._on_mss(_mss(signal="shift", shift_type="bos", direction="up"))
    last = _trend(bus)[-1]
    assert last["metadata"]["confirmations"] == 3, last["metadata"]["confirmations"]
    assert last["score"] > 0
    print(f"OK — تراكم التأكيد: confirmations={last['metadata']['confirmations']} score={last['score']}")


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
    await atom._on_mss(_mss(signal="none"))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_default_from_151_before_mss,
        test_bos_up_sets_uptrend,
        test_choch_sets_transition,
        test_mss_governs_over_151,
        test_confirmations_grow,
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
