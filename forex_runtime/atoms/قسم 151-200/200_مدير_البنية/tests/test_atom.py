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
    "_m200", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_m200"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_PANEL = _mod.EVENT_PANEL
UNIT_IDS = _mod._UNIT_IDS

CFG = {"timeout_seconds": 5}
ACCOUNT = "A"; BROKER = "BR"; SYMBOL = "NQ100"
CID = f"{ACCOUNT}|{BROKER}|{SYMBOL}|60s|1000"


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
        return AtomContext(atom_id=200, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _unit(uid, cid=CID, symbol=SYMBOL, account=ACCOUNT, broker=BROKER,
          timeframe="60s", ready=True, weight=12.5, direction=None):
    return {"symbol": symbol, "account_id": account, "broker": broker,
            "timeframe": timeframe, "id": uid, "cycle_id": cid,
            "period_start": 1000.0,
            "status": "ok", "weight": weight, "ready": ready,
            "direction": direction, "confidence": 70.0}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


async def test_units_open_their_own_cycle_and_complete():
    print("\n--- test_units_open_their_own_cycle_and_complete ---")
    atom, bus = await _new()
    for uid in UNIT_IDS:
        await atom._on_unit_state(_unit(uid, direction=30.0))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert out, "لم تُنشر الدورة المكتملة"
    last = out[-1]
    assert last["complete"] is True and last["present"] == 8, (last["complete"], last["present"])
    assert last["account_id"] == ACCOUNT and last["symbol"] == SYMBOL
    assert "external" in last["results"], "المدقق 209 سيرى external الآن"
    assert last["active_weight"] > 0
    assert not atom._cycles, "الدورة أُغلقت"
    print("OK — وحدات الشمعة تفتح دورتها بنفسها وتكتمل 8/8 — external حاضر")


async def test_no_more_empty_tick_cycles():
    print("\n--- test_no_more_empty_tick_cycles ---")
    atom, bus = await _new()
    # لا يوجد مشترك بالتكة أصلًا — وأي تدفق وحدات لا يفتح دورات فارغة
    await atom._on_unit_state(_unit("swing"))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert not out, "دورة ناقصة لا تُدفع"
    h = await atom.health_check()
    assert h.details["opened"] == 1 and h.details["forwarded"] == 0
    print("OK — لا دورات تكة فارغة: دورة واحدة مفتوحة تنتظر إخوتها بصمت")


async def test_timeout_forwards_partial_declared():
    print("\n--- test_timeout_forwards_partial_declared ---")
    atom, bus = await _new()
    await atom._on_time({"official_time": 2.0})   # الساعة تنبض قبل الفتح (كما في الإنتاج)
    await atom._on_unit_state(_unit("swing"))
    await atom._on_time({"official_time": 10.0})
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert out and out[-1]["complete"] is False, out[-1]["complete"]
    assert "unreported_units" in out[-1] and out[-1]["unreported_units"]
    print("OK — المهلة تدفع الجزئي معلنًا ناقصه، لا صمت")


async def test_late_unit_visible():
    print("\n--- test_late_unit_visible ---")
    atom, bus = await _new()
    for uid in UNIT_IDS:
        await atom._on_unit_state(_unit(uid))
    # وحدة متأخرة على دورة أُغلقت (أعيد إرسال قديم)
    await atom._on_unit_state(_unit("swing"))
    h = await atom.health_check()
    assert h.details["late"] == 1, h.details["late"]
    # لكن سجلها الشخصي محدّث — اللوحة ترى آخر ما قالته
    assert h.details["units_tracked"] == 8
    print("OK — المتأخر معدود ظاهر، وسجله الشخصي حي")


async def test_units_panel_self_declared():
    print("\n--- test_units_panel_self_declared ---")
    atom, bus = await _new()
    for uid in UNIT_IDS:
        await atom._on_unit_state(_unit(uid, direction=20.0))
    # v1.4.0: اللوحة مُقنَّنة — نبضة الثانية تفرّغ المؤجَّل بالقوة.
    await atom._on_time({"official_time": 2.0})
    panels = [p for n, p in bus.published if n == EVENT_PANEL]
    assert panels, "لوحة الوحدات لم تُنشر"
    p1 = panels[-1]
    assert p1["present"] == 8 and not p1["missing"]
    ext = next(r for r in p1["units"] if r["id"] == "external")
    assert ext["next_expected_at"] == 1060.0, ext["next_expected_at"]
    assert ext["deliveries"] == 1 and ext["direction"] == 20.0
    print("OK — كل وحدة تعلن نفسها: إيقاع 60s ووقتها الجاي 1060 واتجاهها")


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
    await atom._on_unit_state(_unit("swing"))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_units_open_their_own_cycle_and_complete,
             test_no_more_empty_tick_cycles,
             test_timeout_forwards_partial_declared,
             test_late_unit_visible,
             test_units_panel_self_declared,
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
