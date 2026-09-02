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
    "_atom507", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom507"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_HALT_REQUEST = _mod.EVENT_HALT_REQUEST
EVENT_DAY = _mod.EVENT_DAY


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
        return AtomContext(atom_id=507, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _new(target=100.0, giveback=100.0):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"daily_profit_target_pct": target, "max_profit_giveback_pct": giveback}))
    await atom.start()
    return atom, bus


def _last(bus, name):
    hits = [p for n, p in bus.published if n == name]
    return hits[-1] if hits else None


async def test_tracks_profit():
    print("\n--- test_tracks_profit ---")
    atom, bus = await _new()
    await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_outcome({"account_id": "A1", "pnl": 30.0, "completeness": "COMPLETE"})
    st = _last(bus, EVENT_OUT)
    assert st["daily_profit"] == 30.0 and st["daily_profit_pct"] == 3.0, st
    assert st["wins"] == 1, st
    print(f"OK — تتبّع ربح اليوم: {st['daily_profit']} = {st['daily_profit_pct']}%")


async def test_target_halts():
    print("\n--- test_target_halts ---")
    atom, bus = await _new(target=5.0)
    await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_outcome({"account_id": "A1", "pnl": 60.0, "completeness": "COMPLETE"})  # 6% >= 5%
    halt = _last(bus, EVENT_HALT_REQUEST)
    assert halt is not None and halt["reason"] == "DAILY_PROFIT_TARGET", halt
    print(f"OK — بلوغ الهدف اليوميّ → halt: {halt['value']}% ≥ {halt['limit']}%")


async def test_giveback_halts():
    print("\n--- test_giveback_halts ---")
    atom, bus = await _new(giveback=2.0)
    await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_outcome({"account_id": "A1", "pnl": 60.0, "completeness": "COMPLETE"})  # peak 6%
    await atom._on_outcome({"account_id": "A1", "pnl": -40.0, "completeness": "COMPLETE"})  # 2%, gave 4%
    halt = _last(bus, EVENT_HALT_REQUEST)
    assert halt is not None and halt["reason"] == "PROFIT_GIVEBACK", halt
    print(f"OK — إعادة الأرباح → halt: أعاد {halt['value']}% ≥ {halt['limit']}%")


async def test_day_resets():
    print("\n--- test_day_resets ---")
    atom, bus = await _new()
    await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_outcome({"account_id": "A1", "pnl": 50.0, "completeness": "COMPLETE"})
    pulse={"pulse_id":"SYS_DAY|86400","bucket_start":86400.0,"official_time":86400.0}
    await atom._on_day(pulse)
    st = _last(bus, EVENT_OUT)
    assert st["daily_profit"] == 0.0 and st["peak_profit_pct"] == 0.0, st
    await atom._on_outcome({"account_id":"A1","pnl":10.0,"completeness":"COMPLETE"})
    await atom._on_day(dict(pulse)); assert atom._books["A1"]["profit"] == 10.0
    snap=await atom.snapshot(); atom2,_=await _new(); await atom2.restore(snap)
    await atom2._on_day(dict(pulse)); assert atom2._books["A1"]["profit"] == 10.0
    print("OK — يوم جديد يصفّر مرة واحدة والتكرار مرفوض بعد الاستعادة")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"daily_profit_target_pct": 5.0, "max_profit_giveback_pct": 2.0}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED  # no account
    await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY → DEGRADED → HEALTHY")


async def main():
    tests = [test_tracks_profit, test_target_halts, test_giveback_halts,
             test_day_resets, test_health_states]
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
