import asyncio
import os
import sys
from pathlib import Path as _Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_m300", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_m300"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"timeout_seconds": 5}
ACCOUNT = "A"; BROKER = "BR"; SYMBOL = "NQ100"


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
        return AtomContext(atom_id=300, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tick(seq, symbol=SYMBOL, account=ACCOUNT, broker=BROKER):
    return {"symbol": symbol, "account_id": account, "broker": broker,
            "price": 10.0, "sequence": seq, "timestamp": float(seq),
            "timeframe": "tick"}


def _unit(uid, cid, symbol=SYMBOL, account=ACCOUNT, broker=BROKER):
    return {"symbol": symbol, "account_id": account, "broker": broker,
            "timeframe": "tick", "id": uid, "cycle_id": cid,
            "status": "ok", "weight": 5.882353, "ready": True}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


UNIT_IDS = _mod._UNIT_IDS            # 17 وحدة كلها بمصادر
CID = f"{ACCOUNT}|{BROKER}|{SYMBOL}|60s|1000"


def _unit(uid, cid=CID, ready=True, weight=6.0, direction=None):
    return {"symbol": SYMBOL, "account_id": ACCOUNT, "broker": BROKER,
            "timeframe": "60s", "id": uid, "cycle_id": cid,
            "period_start": 1000.0,
            "status": "ok", "weight": weight, "ready": ready,
            "direction": direction, "confidence": 60.0}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


async def test_all_17_units_complete_their_own_cycle():
    print("\n--- test_all_17_units_complete_their_own_cycle ---")
    atom, bus = await _new()
    for uid in UNIT_IDS:
        await atom._on_unit_state(_unit(uid, direction=10.0))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert out, "لم تُنشر الدورة المكتملة"
    last = out[-1]
    assert last["complete"] is True and last["present"] == 17
    assert last["expected"] == 17 and last["excluded_units"] == []
    assert not atom._cycles
    print("OK — 17 وحدة تكمل دورتها بنفسها 17/17 — لا مستثنيات")


async def test_no_tick_cycles_partial_waits_quietly():
    print("\n--- test_no_tick_cycles_partial_waits_quietly ---")
    atom, bus = await _new()
    await atom._on_unit_state(_unit("mean"))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert not out, "لا نشر قبل 17/17"
    h = await atom.health_check()
    assert h.details["opened"] == 1 and h.details["forwarded"] == 0
    print("OK — دورة واحدة مفتوحة تنتظر إخوتها بصمت — لا دورات تكة")


async def test_units_panel_and_late_visible():
    print("\n--- test_units_panel_and_late_visible ---")
    atom, bus = await _new()
    await atom._on_unit_state(_unit("zscore", direction=-20.0))
    panels = [p for n, p in bus.published if n == _mod.EVENT_PANEL]
    assert panels
    p1 = panels[-1]
    z = next(r for r in p1["units"] if r["id"] == "zscore")
    assert z["present"] is True and z["next_expected_at"] == 1060.0 and z["deliveries"] == 1
    assert p1["excluded_units"] == [] and len(p1["units"]) == 17
    await atom._on_time({"official_time": 2.0})
    for uid in UNIT_IDS:
        await atom._on_unit_state(_unit(uid))
    await atom._on_unit_state(_unit("zscore"))   # متأخر على دورة أُغلقت
    h = await atom.health_check()
    assert h.details["late"] == 1, h.details["late"]
    print("OK — لوحة الوحدات تعلن والوقت الجاي 1060، والمتأخر ظاهر")


async def test_timeout_forwards_partial_declared():
    print("\n--- test_timeout_forwards_partial_declared ---")
    atom, bus = await _new()
    await atom._on_time({"official_time": 2.0})
    await atom._on_unit_state(_unit("mean"))
    await atom._on_time({"official_time": 10.0})
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert out and out[-1]["complete"] is False and out[-1]["unreported_units"]
    print("OK — المهلة تدفع الجزئي معلنًا ناقصه")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    await atom._on_unit_state(_unit("mean"))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_all_17_units_complete_their_own_cycle,
             test_no_tick_cycles_partial_waits_quietly,
             test_units_panel_and_late_visible,
             test_timeout_forwards_partial_declared,
             test_health_states]
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
