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
    "_atom573", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom573"] = _mod
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
        return AtomContext(atom_id=573, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _stop(buy_stop=None, sell_stop=None, symbol="NQ100"):
    return {"account_id": "A", "broker": "BR", "symbol": symbol, "metadata": {"buy_stop": buy_stop, "sell_stop": sell_stop}}


def _pos(current, side="BUY", entry=100.0, stop=99.0, ticket=7, symbol="NQ100"):
    return {"account_id": "A", "broker": "BR", "positions": [{"account_id": "A", "broker": "BR", "ticket": ticket, "symbol": symbol, "side": side,
            "volume": 0.1, "entry_price": entry, "current_price": current,
            "stop_loss": stop, "take_profit": 0.0, "profit": 0.0}]}


async def _new(cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or {"trail_start_r": 1.0}))
    await atom.start()
    await atom._on_account({"account_id": "A", "broker": "BR"})
    return atom, bus


def _outs(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_trails_to_structure():
    print("\n--- test_trails_to_structure ---")
    atom, bus = await _new()
    await atom._on_stop(_stop(buy_stop=100.5))
    await atom._on_positions(_pos(101.0))  # R=1, buy_stop 100.5 < 101, > sl 99
    o = _outs(bus)[-1]
    assert o["action"] == "MODIFY_SL" and o["stop_loss"] == 100.5
    print("OK — تتبّع للوقف الهيكليّ 100.5")


async def test_only_tighter():
    print("\n--- test_only_tighter ---")
    atom, bus = await _new()
    await atom._on_stop(_stop(buy_stop=100.5))
    await atom._on_positions(_pos(101.0))
    n1 = len(_outs(bus))
    await atom._on_positions(_pos(101.2))  # same structure stop -> no new
    assert len(_outs(bus)) == n1, "نفس الوقف لا يكرّر"
    await atom._on_stop(_stop(buy_stop=100.8))  # tighter
    await atom._on_positions(_pos(101.3))
    assert len(_outs(bus)) == n1 + 1 and _outs(bus)[-1]["stop_loss"] == 100.8
    print("OK — يرسل فقط عند وقف أضيق (100.8)")


async def test_no_trail_before_1r():
    print("\n--- test_no_trail_before_1r ---")
    atom, bus = await _new()
    await atom._on_stop(_stop(buy_stop=100.3))
    await atom._on_positions(_pos(100.5))  # R=0.5
    assert len(_outs(bus)) == 0, "قبل +1R لا تتبّع"
    print("OK — قبل +1R → لا تتبّع")


async def test_sell_trails_down():
    print("\n--- test_sell_trails_down ---")
    atom, bus = await _new()
    await atom._on_stop(_stop(sell_stop=99.5))
    # SELL entry=100 stop=101 risk=1 ; current=99 R=1 ; sell_stop 99.5 > 99, < sl 101
    await atom._on_positions(_pos(99.0, side="SELL", entry=100.0, stop=101.0))
    o = _outs(bus)[-1]
    assert o["stop_loss"] == 99.5 and o["side"] == "SELL"
    print("OK — SELL يتبّع لأسفل 99.5")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"trail_start_r": 1.0}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_positions(_pos(100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة تتدرّج")


async def main():
    tests = [test_trails_to_structure, test_only_tighter, test_no_trail_before_1r,
             test_sell_trails_down, test_health]
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
