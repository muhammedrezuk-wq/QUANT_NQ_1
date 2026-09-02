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
    "_m250", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_m250"] = _mod
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
        return AtomContext(atom_id=250, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tick(seq, symbol=SYMBOL, account=ACCOUNT, broker=BROKER):
    return {"symbol": symbol, "account_id": account, "broker": broker,
            "price": 10.0, "sequence": seq, "timestamp": float(seq),
            "timeframe": "tick"}


def _unit(uid, cid, symbol=SYMBOL, account=ACCOUNT, broker=BROKER):
    return {"symbol": symbol, "account_id": account, "broker": broker,
            "timeframe": "tick", "id": uid, "cycle_id": cid,
            "status": "ok", "weight": 12.5, "ready": True}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


UNIT_IDS = _mod._UNIT_IDS            # 6 وحدات بمصادر (العمق انضم — الدفعة أ)
CID = f"{ACCOUNT}|{BROKER}|{SYMBOL}|60s|1000"


def _unit(uid, cid=CID, ready=True, weight=16.667, direction=None):
    return {"symbol": SYMBOL, "account_id": ACCOUNT, "broker": BROKER,
            "timeframe": "60s", "id": uid, "cycle_id": cid,
            "period_start": 1000.0,
            "status": "ok", "weight": weight, "ready": ready,
            "direction": direction, "confidence": 65.0}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


async def test_sourced_units_complete_their_own_cycle():
    print("\n--- test_sourced_units_complete_their_own_cycle ---")
    atom, bus = await _new()
    for uid in UNIT_IDS:
        await atom._on_unit_state(_unit(uid, direction=-15.0))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert out, "لم تُنشر الدورة المكتملة"
    last = out[-1]
    assert last["complete"] is True and last["present"] == 6, (last["complete"], last["present"])
    assert last["expected"] == 6, "المتوقَّع 6 — العمق انضم والثلاث بلا مصدر مستثنيات"
    assert last["excluded_units"] == ["delta", "cvd", "absorption"]
    assert not atom._cycles
    print("OK — 6 وحدات بمصادر (مع العمق) تكمل 6/6 والثلاث بلا مصدر معلَنات مستثنيات")


async def test_no_tick_cycles_no_waiting_for_sourceless():
    print("\n--- test_no_tick_cycles_no_waiting_for_sourceless ---")
    atom, bus = await _new()
    await atom._on_unit_state(_unit("pool"))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert not out, "لا نشر قبل اكتمال 5/5"
    h = await atom.health_check()
    assert h.details["opened"] == 1 and h.details["excluded_units"] == ["delta", "cvd", "absorption"]
    print("OK — لا دورات تكة، والاستثناء ظاهر بالصحة")


async def test_units_panel_declares_sourceless():
    print("\n--- test_units_panel_declares_sourceless ---")
    atom, bus = await _new()
    await atom._on_unit_state(_unit("fvg", direction=25.0))
    panels = [p for n, p in bus.published if n == _mod.EVENT_PANEL]
    assert panels, "اللوحة لم تُنشر"
    p1 = panels[-1]
    fvg = next(r for r in p1["units"] if r["id"] == "fvg")
    assert fvg["present"] is True and fvg["next_expected_at"] == 1060.0
    delta = next(r for r in p1["units"] if r["id"] == "delta")
    assert delta.get("excluded") is True and delta.get("reason") == "NO_SOURCE_AT_BROKER"
    assert "delta" not in p1["missing"], "المستثنى لا يُنتظر ولا يُعدّ غائبًا"
    print("OK — fvg يعلن نفسه (1000+60=1060) وdelta معلَنة مستثناة بلا مصدر")


async def test_timeout_partial_and_late_visible():
    print("\n--- test_timeout_partial_and_late_visible ---")
    atom, bus = await _new()
    await atom._on_time({"official_time": 2.0})
    await atom._on_unit_state(_unit("pool"))
    await atom._on_time({"official_time": 10.0})
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert out and out[-1]["complete"] is False and out[-1]["unreported_units"]
    await atom._on_unit_state(_unit("pool"))   # متأخر على دورة أُغلقت
    h = await atom.health_check()
    assert h.details["late"] == 1, h.details["late"]
    print("OK — المهلة تدفع الجزئي معلنًا، والمتأخر ظاهر")


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
    await atom._on_unit_state(_unit("pool"))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_sourced_units_complete_their_own_cycle,
             test_no_tick_cycles_no_waiting_for_sourceless,
             test_units_panel_declares_sourceless,
             test_timeout_partial_and_late_visible,
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
