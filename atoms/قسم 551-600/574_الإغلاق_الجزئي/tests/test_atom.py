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
    "_atom574", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom574"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"partial_at_r": 1.0, "partial_fraction": 0.5, "min_partial_lot": 0.01}


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
        return AtomContext(atom_id=574, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _pos(current, volume=0.2, side="BUY", entry=100.0, stop=99.0, ticket=7):
    return {"positions": [{"ticket": ticket, "symbol": "NQ100", "side": side,
            "volume": volume, "entry_price": entry, "current_price": current,
            "stop_loss": stop, "take_profit": 0.0, "profit": 0.0}]}


async def _new(cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    return atom, bus


def _outs(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_partial_at_1r():
    print("\n--- test_partial_at_1r ---")
    atom, bus = await _new()
    await atom._on_positions(_pos(100.5))  # R=0.5
    assert len(_outs(bus)) == 0
    await atom._on_positions(_pos(101.0))  # R=1.0 -> close 0.2*0.5=0.1
    o = _outs(bus)[-1]
    assert o["action"] == "CLOSE_PARTIAL" and o["volume"] == 0.1, o["volume"]
    print("OK — +1R → CLOSE_PARTIAL 0.1 (نصف 0.2)")


async def test_fires_once():
    print("\n--- test_fires_once ---")
    atom, bus = await _new()
    await atom._on_positions(_pos(101.0))
    await atom._on_positions(_pos(102.0))
    assert len(_outs(bus)) == 1
    print("OK — جزئيّ مرّة واحدة")


async def test_too_small_skips():
    print("\n--- test_too_small_skips ---")
    atom, bus = await _new()
    # volume 0.01 -> 0.005 < min 0.01 -> no emit
    await atom._on_positions(_pos(101.0, volume=0.01))
    # عقد ٩٥-٥ (من نظام المالك اليدويّ · اعتُمد 2026-08-16): البقيّة الأصغر من
    # الحدّ الأدنى رجلٌ لا تُغلق ولا تُدار — فتُغلق الرجل كاملةً بدل تجزئتها.
    outs = _outs(bus)
    assert len(outs) == 1, "حجم صغير: يُغلق كاملًا لا يُجزّأ"
    assert outs[-1]["action"] == "CLOSE" and outs[-1]["volume"] == 0.01
    assert outs[-1]["last_lot_guard"] is True, "حارس آخر لوت معلَن"
    print("OK — حجم صغير جدًّا → لا جزئيّ")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_positions(_pos(100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة تتدرّج")


async def main():
    tests = [test_partial_at_1r, test_fires_once, test_too_small_skips, test_health]
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
