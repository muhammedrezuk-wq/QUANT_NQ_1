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
    "_atom572", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom572"] = _mod
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
        return AtomContext(atom_id=572, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _pos(current, side="BUY", entry=100.0, stop=99.0, ticket=7, symbol="NQ100"):
    return {"positions": [{"ticket": ticket, "symbol": symbol, "side": side,
            "volume": 0.1, "entry_price": entry, "current_price": current,
            "stop_loss": stop, "take_profit": 0.0, "profit": 0.0}]}


async def _new(cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or {"breakeven_at_r": 1.0}))
    await atom.start()
    return atom, bus


def _outs(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_breakeven_at_1r():
    print("\n--- test_breakeven_at_1r ---")
    atom, bus = await _new()
    # first sighting fixes initial risk = |100-99| = 1
    await atom._on_positions(_pos(100.5))  # R=0.5 -> no
    assert len(_outs(bus)) == 0
    await atom._on_positions(_pos(101.0))  # R=1.0 -> breakeven
    o = _outs(bus)[-1]
    assert o["action"] == "MODIFY_SL" and o["stop_loss"] == 100.0
    assert o["reason"] == "breakeven"
    print("OK — +1R → MODIFY_SL للدخول 100")


async def test_fires_once():
    print("\n--- test_fires_once ---")
    atom, bus = await _new()
    await atom._on_positions(_pos(101.0))
    await atom._on_positions(_pos(102.0))
    assert len(_outs(bus)) == 1, "التعادل مرّة واحدة"
    print("OK — يُطلق مرّة واحدة")


async def test_sell_side():
    print("\n--- test_sell_side ---")
    atom, bus = await _new()
    # SELL entry=100 stop=101 risk=1 ; current=99 -> profit_dist=1 R=1
    await atom._on_positions(_pos(99.0, side="SELL", entry=100.0, stop=101.0))
    o = _outs(bus)[-1]
    assert o["stop_loss"] == 100.0 and o["side"] == "SELL"
    print("OK — SELL: تعادل للدخول 100")


async def test_cleanup_on_vanish():
    print("\n--- test_cleanup_on_vanish ---")
    atom, bus = await _new()
    await atom._on_positions(_pos(101.0))
    await atom._on_positions({"positions": []})  # vanished
    assert len(atom._tracked) == 0, "يُنظّف الصفقة المختفية"
    print("OK — تنظيف عند الاختفاء")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"breakeven_at_r": 1.0}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_positions(_pos(100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة تتدرّج")


async def main():
    tests = [test_breakeven_at_1r, test_fires_once, test_sell_side,
             test_cleanup_on_vanish, test_health]
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
