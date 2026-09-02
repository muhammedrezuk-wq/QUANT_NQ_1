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
    "_m150", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_m150"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
MODE_LIVE = _mod.MODE_LIVE

ACCOUNT = "A"; BROKER = "BR"; SYMBOL = "NQ100"
UNIT_IDS = ["trend", "momentum", "volatility", "volume", "spread", "candle",
            "gap", "session", "time", "velocity", "acceleration",
            "volume_quality", "noise", "correlation", "relative_strength"]


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
        return AtomContext(atom_id=150, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _live_state(unit_id, sequence, signal="up"):
    # مسار حي (على التكة) — بنفس shape المحللين عبر live_analysis.
    return {"id": unit_id, "analyzer_id": unit_id, "account_id": ACCOUNT,
            "broker": BROKER, "symbol": SYMBOL, "sequence": sequence,
            "analysis_mode": MODE_LIVE, "status": "ok", "signal": signal,
            "score": 70, "confidence": 80.0, "weight": 6.6667,
            "ready": True, "analysis_state": "DECISION_READY",
            "source_timestamp": float(sequence)}


async def _make():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"timeout_seconds": 5.0,
                                            "live_flush_timeout_s": 1.0}))
    await atom.start()
    return atom, bus


async def test_live_all_units_flushes():
    print("\n--- test_live_all_units_flushes ---")
    atom, bus = await _make()
    # أرسل كل الوحدات الحي على نفس sequence → يجمع batch كامل → ينشر.
    for uid in UNIT_IDS:
        await atom._on_live_state(_live_state(uid, 1), uid, uid)
    out = [p for n, p in bus.published if n == EVENT_OUT]
    assert out, "لم ينشر المسار الحي"
    last = out[-1]
    assert last["analysis_mode"] == MODE_LIVE, last["analysis_mode"]
    assert last["complete"] is True, last["complete"]
    assert last["active_weight"] > 0, last["active_weight"]
    assert last["cycle_id"].endswith("|tick|1"), last["cycle_id"]
    print("OK — المسار الحي يجمّع وينشر على التكة, cycle=%s, weight=%s" % (last["cycle_id"], last["active_weight"]))


async def test_live_cycle_identity():
    print("\n--- test_live_cycle_identity ---")
    atom, bus = await _make()
    await atom._on_live_state(_live_state("trend", 7), "trend", "trend")
    await atom._on_live_state(_live_state("momentum", 7), "momentum", "momentum")
    out = [p for n, p in bus.published if n == EVENT_OUT]
    # قد لا يكتمل batch بعد (15 وحدة) — لكن cycle_id يجب أن يكون من sequence 7
    batch = atom._live_batch.get((ACCOUNT, BROKER, SYMBOL))
    assert batch is not None and batch["sequence"] == 7, "batch لم يُفتح من sequence 7"
    print("OK — المسار الحي مفتوح على sequence 7")


class GatedBus(FakeEventBus):
    """v2.9.0 proof: gate publish() on the OLD batch's forced flush so a
    second concurrent unit for the same scope gets a real window to run
    while the first is suspended mid-flush -- reproduces the exact
    interleaving the fix targets, not a timing-dependent guess."""
    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
        self.reached_gate = asyncio.Event()

    async def publish(self, name, payload):
        if name == EVENT_OUT and payload.get("close_reason") == "tick_batch_newer_tick":
            self.reached_gate.set()
            await self.gate.wait()
        self.published.append((name, payload))


async def test_concurrent_units_same_scope_no_lost_update():
    print("\n--- test_concurrent_units_same_scope_no_lost_update ---")
    bus = GatedBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"timeout_seconds": 5.0,
                                            "live_flush_timeout_s": 1.0}))
    await atom.start()
    scope = (ACCOUNT, BROKER, SYMBOL)
    # افتح batch على تكة 1 بوحدة واحدة -- ناقص، لن يكتمل من تلقاء نفسه.
    await atom._on_live_state(_live_state("trend", 1), "trend", "trend")
    assert atom._live_batch[scope]["arrived"] == {"trend"}

    # تزامن حقيقي: momentum يصل بتكة أحدث (2) فيُجبَر تفريغ batch التكة
    # القديمة (newer_tick) -- يعلَّق داخل publish. volatility يصل بنفس
    # التكة (2) بينما momentum معلَّق -- بالضبط "تزامن وحدتين لنفس النطاق".
    momentum_task = asyncio.create_task(
        atom._on_live_state(_live_state("momentum", 2), "momentum", "momentum"))
    await bus.reached_gate.wait()
    volatility_task = asyncio.create_task(
        atom._on_live_state(_live_state("volatility", 2), "volatility", "volatility"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    bus.gate.set()
    await asyncio.wait_for(asyncio.gather(momentum_task, volatility_task), timeout=5.0)

    # الباقي (13 وحدة) يصل على نفس التكة (2) -- المجموع الصحيح صار 15/15.
    remaining = [u for u in UNIT_IDS if u not in ("momentum", "volatility")]
    for uid in remaining:
        await atom._on_live_state(_live_state(uid, 2), uid, uid)

    out2 = [p for n, p in bus.published if n == EVENT_OUT
            and p.get("sequence") == 2 and p.get("close_reason") == "tick_batch_all_units"]
    assert out2, ("فقدان تحديث مؤكَّد: التكة 2 لم تكتمل فوراً رغم وصول الخمس "
                  "عشرة وحدة كلها -- عضويّة وحدة (volatility) في "
                  "batch['arrived'] ضاعت بتصادم إنشاء batch جديد بعد await "
                  "الفلَش (v2.9.0 لم يُصلَح أو انكسر)")
    print("OK — تزامن وحدتين على نفس النطاق لا يُفقد أي عضويّة، الدفعة تكتمل فوراً بلا مهلة")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"timeout_seconds": 5.0}))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    print("OK — الصحة: UNHEALTHY→DEGRADED (قبل أي نتيجة حية)")


async def test_analysts_panel_self_declared():
    print("\n--- test_analysts_panel_self_declared ---")
    atom, bus = await _make()
    for uid in UNIT_IDS:
        row = {**_live_state(uid, 1), "direction": 20.0}
        await atom._on_live_state(row, uid, uid)
    panels = [p for n, p in bus.published if n == _mod.EVENT_PANEL]
    assert panels, "اللوحة لم تُنشر"
    p1 = panels[-1]
    assert p1["present"] == 15 and not p1["missing"], (p1["present"], p1["missing"])
    trend = next(r for r in p1["analysts"] if r["id"] == "trend")
    assert trend["mode"] == MODE_LIVE and trend["deliveries"] == 1, trend
    assert trend["direction"] == 20.0 and trend["weight"] == 6.6667, trend
    assert trend["next_expected_at"] is None, "التكة مستمرة لا وقت جاي"
    # مسار الشمعة: المحلل نفسه يعلن فريمه ووقته الجاي
    await atom._on_candle_state({"id": "trend", "analyzer_id": "trend",
        "account_id": ACCOUNT, "broker": BROKER, "symbol": SYMBOL,
        "timeframe": "60s", "cycle_id": "c1", "period_start": 1000.0,
        "direction": 30.0, "confidence": 55.0, "weight": 6.6667,
        "ready": False, "analysis_state": "NOT_READY"}, "trend")
    await atom._on_candle_closed({"account_id": ACCOUNT, "broker": BROKER,
        "symbol": SYMBOL, "timeframe": "60s", "period_start": 1000.0})
    p2 = [p for n, p in bus.published if n == _mod.EVENT_PANEL][-1]
    trend2 = next(r for r in p2["analysts"] if r["id"] == "trend")
    assert trend2["mode"] == _mod.MODE_CANDLE, trend2
    assert trend2["next_expected_at"] == 1060.0, trend2["next_expected_at"]
    assert trend2["deliveries"] == 2, trend2["deliveries"]
    h = await atom.health_check()
    assert h.details["panel_emitted"] >= 2 and h.details["analysts_tracked"] >= 15, h.details
    print("OK — لوحة المحللين: كل محلل يعلن بنفسه (تكة مستمرة، شمعة بوقت جاي 1000+60=1060)")


async def main():
    tests = [test_live_all_units_flushes,
             test_live_cycle_identity,
             test_concurrent_units_same_scope_no_lost_update,
             test_health_states,
             test_analysts_panel_self_declared]
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
