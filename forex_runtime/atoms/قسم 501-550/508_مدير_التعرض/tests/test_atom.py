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
    "_atom508", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom508"] = _mod
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
        return AtomContext(atom_id=508, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _new(max_pct=50.0):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"max_exposure_pct": max_pct}))
    await atom.start()
    return atom, bus


def _specs(**sizes):
    return {"symbols": [{"account_id": "A1", "symbol": s, "contract_size": c} for s, c in sizes.items()]}


def _positions(*rows):
    return {"account_id": "A1", "broker": "BR", "positions": [
        {"account_id": a, "broker": "BR", "symbol": s, "volume": v, "current_price": p}
        for (a, s, v, p) in rows], "timestamp": 5.0, "complete": True,
        "usable_for_new_exposure": True, "usable_for_protection": True}


def _last(bus, name, account_id=None):
    hits = [p for n, p in bus.published if n == name
            and (account_id is None or p.get("account_id") == account_id)]
    return hits[-1] if hits else None


async def test_computes_exposure():
    print("\n--- test_computes_exposure ---")
    atom, bus = await _new(max_pct=50.0)
    await atom._on_specs(_specs(X=1.0))
    await atom._on_account({"account_id": "A1", "broker": "BR"});await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_positions(_positions(("A1", "X", 2.0, 100.0)))
    st = _last(bus, EVENT_OUT, "A1")
    assert st["notional"] == 200.0 and st["exposure_pct"] == 20.0, st
    assert st["open_positions"] == 1 and st["breached"] is False, st
    print(f"OK — تعرّض محسوب: notional={st['notional']} exposure={st['exposure_pct']}%")


async def test_breach_halts():
    print("\n--- test_breach_halts ---")
    atom, bus = await _new(max_pct=50.0)
    await atom._on_specs(_specs(X=1.0))
    await atom._on_account({"account_id": "A1", "broker": "BR"});await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_positions(_positions(("A1", "X", 6.0, 100.0)))  # 600/1000 = 60% >= 50%
    halt = _last(bus, EVENT_HALT_REQUEST, "A1")
    assert halt is not None and halt["reason"] == "MAX_EXPOSURE", halt
    assert halt["exposure_pct"] == 60.0, halt
    print(f"OK — تجاوز التعرّض → emergency.halt: {halt['exposure_pct']}% ≥ {halt['limit']}%")


async def test_no_double_halt():
    print("\n--- test_no_double_halt ---")
    atom, bus = await _new(max_pct=50.0)
    await atom._on_specs(_specs(X=1.0))
    await atom._on_account({"account_id": "A1", "broker": "BR"});await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_positions(_positions(("A1", "X", 6.0, 100.0)))
    await atom._on_positions(_positions(("A1", "X", 7.0, 100.0)))  # still breached
    halts = [p for n, p in bus.published if n == EVENT_HALT_REQUEST]
    assert len(halts) == 1, halts
    print("OK — لا halt مكرّر وهو ما زال متجاوزًا (حافة)")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"max_exposure_pct": 50.0}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED  # no specs
    await atom._on_specs(_specs(X=1.0))
    assert (await atom.health_check()).state == HealthState.DEGRADED  # no account
    await atom._on_account({"account_id": "A1", "broker": "BR"});await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    ready = await atom.health_check()
    assert ready.state == HealthState.HEALTHY and ready.message.startswith("READY")
    await atom._on_positions(_positions(("A1", "X", 1.0, 100.0)))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY → DEGRADED(specs) → DEGRADED(account) → HEALTHY(جاهز، صفر نطاق) → HEALTHY")


def _positions_sided(*rows):
    return {"account_id": "A1", "broker": "BR", "positions": [
        {"account_id": a, "broker": "BR", "symbol": s, "volume": v,
         "current_price": p, "side": side}
        for (a, s, v, p, side) in rows], "timestamp": 5.0, "complete": True,
        "usable_for_new_exposure": True, "usable_for_protection": True}


async def test_owner_ruling_gross_net_hedge():
    # حكم المالك ٢٥-٠٨: الإجمالي عدّاد الحراسة، الصافي للاتجاه فقط.
    print("\n--- test_owner_ruling_gross_net_hedge ---")
    atom, bus = await _new(max_pct=50.0)
    await atom._on_specs(_specs(X=1.0))
    await atom._on_account({"account_id": "A1", "broker": "BR"});await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_positions(_positions_sided(("A1", "X", 2.0, 100.0, "BUY"),
                                              ("A1", "X", 2.0, 100.0, "SELL")))
    st = _last(bus, EVENT_OUT, "A1")
    assert st["gross_exposure"] == 400.0, st
    assert st["net_exposure"] == 0.0, st
    assert st["hedge_ratio"] == 1.0, st
    assert st["breached"] is False and st["usable_for_new_exposure"] is True, st
    print("OK — زوج متعادل: gross=400 net=0 hedge=1.0 (الإجمالي لا يُرى صفرًا)")


async def test_breach_blocks_new_exposure_only():
    # الاختراق يمنع التعرّض الجديد فقط: usable_for_new_exposure=False
    # بينما الحماية والمخاطر تبقيان صالحتين (الإغلاق لا يمرّ بهذه البوابة).
    print("\n--- test_breach_blocks_new_exposure_only ---")
    atom, bus = await _new(max_pct=30.0)
    await atom._on_specs(_specs(X=1.0))
    await atom._on_account({"account_id": "A1", "broker": "BR"});await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_positions(_positions_sided(("A1", "X", 2.0, 100.0, "BUY"),
                                              ("A1", "X", 2.0, 100.0, "SELL")))  # gross 40% >= 30%
    st = _last(bus, EVENT_OUT, "A1")
    assert st["breached"] is True, st
    assert st["usable_for_new_exposure"] is False, st
    assert st["usable_for_risk"] is True and st["usable_for_protection"] is True, st
    health = await atom.health_check()
    assert "EXPOSURE_BREACHED" in health.message and "hedge=1.0" in health.message, health.message
    print("OK — اختراق الإجمالي: الجديد ممنوع، الحماية والإدارة تعملان")


async def test_net_shows_direction():
    print("\n--- test_net_shows_direction ---")
    atom, bus = await _new(max_pct=90.0)
    await atom._on_specs(_specs(X=1.0))
    await atom._on_account({"account_id": "A1", "broker": "BR"});await atom._on_truth_equity({"account_id": "A1", "broker": "BR", "equity": 1000.0})
    await atom._on_positions(_positions_sided(("A1", "X", 6.0, 100.0, "buy")))
    st = _last(bus, EVENT_OUT, "A1")
    assert st["gross_exposure"] == 600.0 and st["net_exposure"] == 600.0, st
    assert st["hedge_ratio"] == 0.0, st
    # صف بلا side → الصافي مجهول بأمانة، الإجمالي يبقى محسوبًا
    await atom._on_positions(_positions(("A1", "X", 6.0, 100.0)))
    st = _last(bus, EVENT_OUT, "A1")
    assert st["net_exposure"] is None and st["hedge_ratio"] is None, st
    assert st["gross_exposure"] == 600.0, st
    print("OK — الصافي اتجاه (عاري=hedge 0.0)، وبلا side لا يُخترع صفر")


async def main():
    tests = [test_computes_exposure, test_breach_halts, test_no_double_halt,
             test_health_states, test_owner_ruling_gross_net_hedge,
             test_breach_blocks_new_exposure_only, test_net_shows_direction]
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
