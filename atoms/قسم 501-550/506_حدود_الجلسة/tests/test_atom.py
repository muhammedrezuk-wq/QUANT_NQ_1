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
    "_atom506", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom506"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_HALT_REQUEST = _mod.EVENT_HALT_REQUEST


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

    def make_context(self, cfg):
        return AtomContext(atom_id=506, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _new(loss=3.0, trades=2, dd=10.0):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"max_session_loss_pct": loss, "max_session_trades": trades,
         "max_equity_drawdown_pct": dd}))
    await atom.start()
    return atom, bus


def _last(bus, name):
    hits = [p for n, p in bus.published if n == name]
    return hits[-1] if hits else None


async def test_session_trades_halt():
    print("\n--- test_session_trades_halt ---")
    atom, bus = await _new(trades=2)
    await atom._on_session({"signal": "london"})
    await atom._on_trade({"event_type": "OPENED", "account_id": "A1"})
    await atom._on_trade({"event_type": "OPENED", "account_id": "A1"})
    halt = _last(bus, EVENT_HALT_REQUEST)
    assert halt is not None and halt["reason"] == "MAX_SESSION_TRADES", halt
    print(f"OK — تجاوز صفقات الجلسة → halt: {halt['value']} ≥ {halt['limit']}")


async def test_session_loss_halt():
    print("\n--- test_session_loss_halt ---")
    atom, bus = await _new(loss=3.0)
    await atom._on_session({"signal": "london"})
    await atom._on_loss({"account_id": "A1", "loss_pct": 3.5, "completeness": "COMPLETE"})
    halt = _last(bus, EVENT_HALT_REQUEST)
    assert halt is not None and halt["reason"] == "SESSION_LOSS_LIMIT", halt
    print(f"OK — تجاوز خسارة الجلسة → halt: {halt['value']}% ≥ {halt['limit']}%")


async def test_drawdown_halt():
    print("\n--- test_drawdown_halt ---")
    atom, bus = await _new(dd=10.0)
    await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})  # peak — من 654
    await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 850.0})   # -15%
    halt = _last(bus, EVENT_HALT_REQUEST)
    assert halt is not None and halt["reason"] == "EQUITY_DRAWDOWN_LIMIT", halt
    print(f"OK — سحب الحقوق → halt: {halt['value']}% ≥ {halt['limit']}%")


async def test_session_change_resets():
    print("\n--- test_session_change_resets ---")
    atom, bus = await _new(trades=5)
    await atom._on_session({"signal": "london"})
    await atom._on_trade({"event_type": "OPENED", "account_id": "A1"})
    await atom._on_session({"signal": "new_york"})  # session change → reset
    st = _last(bus, EVENT_OUT)
    assert st["session"] == "new_york" and st["session_trades"] == 0, st
    print("OK — تغيّر الجلسة يصفّر عدّاد صفقات الجلسة")


async def test_ignores_non_time_session():
    print("\n--- test_ignores_non_time_session ---")
    atom, bus = await _new(trades=5)
    await atom._on_session({"signal": "london"})
    await atom._on_trade({"event_type": "OPENED", "account_id": "A1"})
    await atom._on_session({"signal": "crypto_24h"})  # ليست جلسة زمنيّة → تُتجاهَل
    st = _last(bus, EVENT_OUT)
    assert st["session"] == "london" and st["session_trades"] == 1, st
    print("OK — crypto_24h/open (خاصّة برمز) لا تغيّر الجلسة الزمنيّة")


async def test_duplicate_day_pulse_is_ignored_after_restore():
    atom, _ = await _new(trades=10)
    await atom._on_session({"signal": "london"})
    await atom._on_trade({"event_type": "OPENED", "account_id": "A1"})
    pulse = {"pulse_id": "SYS_DAY|86400", "bucket_start": 86400.0,
             "official_time": 86400.0}
    await atom._on_day(pulse)
    await atom._on_trade({"event_type": "OPENED", "account_id": "A1"})
    assert atom._books["A1"]["session_trades"] == 1
    await atom._on_day(dict(pulse))
    assert atom._books["A1"]["session_trades"] == 1
    snap = await atom.snapshot()
    atom2, _ = await _new(trades=10)
    await atom2.restore(snap)
    await atom2._on_day(dict(pulse))
    assert atom2._books["A1"]["session_trades"] == 1


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"max_session_loss_pct": 3.0, "max_session_trades": 2,
         "max_equity_drawdown_pct": 10.0}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED  # no account
    await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY → DEGRADED → HEALTHY")


async def main():
    tests = [test_session_trades_halt, test_session_loss_halt, test_drawdown_halt,
             test_session_change_resets, test_ignores_non_time_session,
             test_duplicate_day_pulse_is_ignored_after_restore, test_health_states]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e))); print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e))); print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}"); sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
