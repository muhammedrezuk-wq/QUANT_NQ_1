import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom166", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom166"] = _mod
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

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=166, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _unit(uid, signal, score, conf, status="ok"):
    return {"id": uid, "status": status, "signal": signal, "score": score, "confidence": conf}


def _collected(results, symbol="NQ100", cid="NQ100|60s|100.0"):
    # هوية كاملة (حساب+وسيط) — الذرّة تعلن IDENTITY_INCOMPLETE بصدق عند
    # غيابها (عقد ٩٠-٣١)، والحارس يغذّيها مدخلًا مكتمل الهوية كالواقع.
    normalized = {key: {**row, "cycle_id": cid, "symbol": symbol, "timeframe": "60s",
                        "account_id": "A1", "broker": "B1"}
                  for key, row in results.items()}
    return {"cycle_id": cid, "symbol": symbol, "timeframe": "60s",
            "account_id": "A1", "broker": "B1",
            "results": normalized, "expected": len(normalized), "present": len(normalized),
            "complete": True, "cycle_status": "complete"}


async def _make(cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or {"agree_threshold": 0.2}))
    await atom.start()
    return atom, bus


def _out(bus):
    # أحداث دورة الشموع القديمة (عقد 451) — جسم القسم المدموج يحمل
    # timeframe="section" ويُقرأ بحارسه الخاص، لا هنا.
    return [p for n, p in bus.published
            if n == EVENT_OUT and p.get("timeframe") != "section"]


def _section(bus):
    return [p for n, p in bus.published
            if n == EVENT_OUT and p.get("timeframe") == "section"]


def _fast(bus):
    return [p for n, p in bus.published if n == _mod.EVENT_FAST]


def _slow(bus):
    return [p for n, p in bus.published if n == _mod.EVENT_SLOW]


async def test_agreement_up():
    print("\n--- test_agreement_up ---")
    atom, bus = await _make()
    await atom._on_collected(_collected({
        "trend": _unit("trend", "up", 80, 0.9),
        "momentum": _unit("momentum", "up", 70, 0.8)}))
    o = _out(bus)[-1]
    assert o["status"] == "ok" and o["signal"] == "up", o
    assert o["agreement"] == 1.0
    assert o["confidence"] > 0.7
    assert not o["warnings"]
    print(f"OK — إجماع صاعد: signal={o['signal']} agree={o['agreement']} conf={o['confidence']} score={o['score']}")


async def test_conflict_flagged():
    print("\n--- test_conflict_flagged ---")
    atom, bus = await _make()
    await atom._on_collected(_collected({
        "trend": _unit("trend", "up", 80, 0.85),
        "momentum": _unit("momentum", "down", 75, 0.8)}))
    o = _out(bus)[-1]
    # near-equal opposing votes → sideways (net within threshold) OR weak winner
    assert any(w.startswith("conflict:") for w in o["warnings"]) or o["signal"] == "sideways", o
    print(f"OK — تعارض: signal={o['signal']} warnings={o['warnings']}")


async def test_no_valid_insufficient():
    print("\n--- test_no_valid_insufficient ---")
    atom, bus = await _make()
    await atom._on_collected(_collected({
        "trend": _unit("trend", "up", 0, 0.0, status="insufficient_data")}))
    o = _out(bus)[-1]
    assert o["status"] == "insufficient_data" and o["signal"] == "sideways"
    assert "no_valid_analysis" in o["warnings"]
    print("OK — لا وحدات صالحة → insufficient")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    atom, bus = await _make()
    await atom._on_collected(_collected({
        "trend": _unit("trend", "up", 80, 0.9),
        "momentum": _unit("momentum", "up", 60, 0.7)}))
    o = _out(bus)[-1]
    for f in ("symbol", "cycle_id", "id", "status", "signal", "score", "confidence",
              "quality", "warnings", "contributors", "agreement", "metadata"):
        assert f in o, f"حقل ناقص: {f}"
    assert o["id"] == "fusion"
    assert set(o["contributors"].keys()) == {"trend", "momentum"}
    print("OK — عقد الدمج كامل + contributors")


async def test_context_units_not_voted():
    print("\n--- test_context_units_not_voted ---")
    atom, bus = await _make()
    await atom._on_collected(_collected({
        "trend": _unit("trend", "up", 80, 0.9),
        "volatility": _unit("volatility", "high", 60, 0.8)}))
    o = _out(bus)[-1]
    assert o["signal"] == "up", o
    assert o["agreement"] == 1.0
    assert "volatility" in o["contributors"]
    print("OK — وحدة السياق (volatility) ما تصوّت على الاتجاه، بس تظهر بالمساهمين")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"agree_threshold": 0.2}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_collected(_collected({"trend": _unit("trend", "up", 80, 0.9)}))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


def _live_row(uid, score, strength=None, seq=7):
    return {"analyzer_id": uid, "id": uid, "account_id": "A1", "broker": "B1",
            "symbol": "NQ100", "sequence": seq, "weight": 100.0 / 15.0,
            "confidence": 80.0, "current_depth": 90.0, "required_depth": 60.0,
            "confidence_threshold": 60.0, "source_timestamp": 1000.0,
            "timestamp": 1000.1, "ready": True,
            "analysis_state": _mod.STATE_READY, "score": score,
            **({"strength": strength} if strength is not None else {})}


def _live_collected(score, strength=70.0):
    ids = list(_mod.DEFAULT_WEIGHTS)
    return {"analysis_mode": _mod.MODE_LIVE, "account_id": "A1", "broker": "B1",
            "symbol": "NQ100", "cycle_id": "A1|B1|NQ100|tick|7", "sequence": 7,
            "source_timestamp": 1000.0, "timestamp": 1000.5,
            "results": {uid: _live_row(uid, score, strength) for uid in ids}}


async def test_fast_card_eight_fields():
    # حارس بند أ٢ (بختم NQ): المجمّع السريع ينشر العقد الثماني كاملًا.
    print("\n--- test_fast_card_eight_fields ---")
    atom, bus = await _make()
    await atom._on_collected(_live_collected(score=80.0, strength=70.0))
    cards = _fast(bus)
    assert cards, "بطاقة المسار السريع لم تُنشر"
    card = cards[-1]
    for field in ("direction", "strength", "confidence", "current_depth",
                  "required_depth", "weight", "ratio", "state", "unknown_fields"):
        assert field in card, f"حقل ناقص بالسريع: {field}"
    assert card["path"] == "fast" and abs(card["direction"] - 80.0) < 1e-6
    assert abs(card["strength"] - 70.0) < 1e-6 and card["state"] == "READY"
    assert card["unknown_fields"] == []
    print("OK — السريع: ثمانية كاملة، اتجاه 80، قوة 70، READY")


async def test_slow_card_and_section_merge_55_45():
    # حارس بندي أ٣/أ٤ (بختم NQ): البطيء بالعقد نفسه، والدمج 55/45 —
    # مثال المالك الحسابي: 80×0.55 + 40×0.45 = 62.
    print("\n--- test_slow_card_and_section_merge_55_45 ---")
    atom, bus = await _make()
    await atom._on_collected(_live_collected(score=80.0))
    ids = list(_mod.DEFAULT_WEIGHTS)
    await atom._on_collected(_collected(
        {uid: _unit(uid, "up", 40, 0.9) for uid in ids}))
    slow = _slow(bus)[-1]
    assert slow["path"] == "slow" and abs(slow["direction"] - 40.0) < 1e-6
    assert "strength" in slow["unknown_fields"], "قوة الشموع غائبة فتُعلن مجهولة"
    section = _section(bus)[-1]
    assert abs(section["direction"] - 62.0) < 1e-6, section["direction"]
    # عطل مقاس بعد النشر الحي: 451 يرفض حمولة حية بلا وسيط — الهوية تمر كاملة.
    assert section["account_id"] == "A1" and section["broker"] == "B1"
    assert section["path_weights"] == {"fast": 55.0, "slow": 45.0}
    assert section["metadata"]["present_paths"] == ["fast", "slow"]
    eight = section["section_contract"]
    assert abs(eight["direction"] - 62.0) < 1e-6
    print(f"OK — الدمج: 80×0.55 + 40×0.45 = {section['direction']}")


async def test_missing_path_declared_not_hidden():
    # مسار غائب = missing_path معلَنًا بوزنه — لا انتظار ولا إخفاء.
    print("\n--- test_missing_path_declared_not_hidden ---")
    atom, bus = await _make()
    await atom._on_collected(_live_collected(score=80.0))
    section = _section(bus)[-1]
    assert section["metadata"]["present_paths"] == ["fast"]
    assert abs(section["path_missing_weight"] - 45.0) < 1e-6
    assert "missing_path" in section["warnings"]
    assert abs(section["direction"] - 80.0) < 1e-6, "السريع وحده = قيمته بلا تمييع"
    print("OK — البطيء غائب: معلَن بوزنه 45 والسريع يمرّ بقيمته")


async def test_path_weight_rebalance_q2():
    # حارس ق٢ (بختم NQ): تعديل وزن مسار يعيد توزيع الآخر = 100 − القيمة.
    # معزول عن سجل المُعامِلات الحي: بديل apply_command يحاكي الاعتماد فقط —
    # (درس مقيس: النسخة الأولى كتبت اعتمادًا حقيقيًّا 70/30 بالقاعدة الحية ونُظّف).
    print("\n--- test_path_weight_rebalance_q2 ---")
    atom, bus = await _make()
    real_apply = _mod.apply_command

    def fake_apply(payload, *, atom_id, registry=None):
        name = str(payload.get("name") or "")
        if name not in ("ANALYSIS_FAST_WEIGHT", "ANALYSIS_SLOW_WEIGHT"):
            return None
        if not payload.get("command_id") or not payload.get("operator"):
            return None
        return {"name": name, "value": float(payload["value"]), "version": 1}

    _mod.apply_command = fake_apply
    try:
        await atom._on_dial_command({
            "name": "ANALYSIS_FAST_WEIGHT", "value": 70.0,
            "command_id": "test-rebalance-1", "operator": "NQ-test",
            "approved_at": 1000.0})
    finally:
        _mod.apply_command = real_apply
    assert abs(atom._fast_weight - 70.0) < 1e-6
    assert abs(atom._slow_weight - 30.0) < 1e-6, atom._slow_weight
    assert abs(atom._fast_weight + atom._slow_weight - 100.0) < 1e-6
    print("OK — سريع 70 ⇒ بطيء 30 والمجموع 100 (بلا لمس السجل الحي)")


async def test_readiness_pct_gradual():
    a = Atom()
    e1 = a._eight(50.0, 40.0, 30.0, 36.0, 60.0, 16.6, "NOT_READY")
    e2 = a._eight(50.0, 40.0, 30.0, 48.0, 60.0, 16.6, "NOT_READY")
    e3 = a._eight(50.0, 40.0, 30.0, 60.0, 60.0, 16.6, "READY")
    e4 = a._eight(50.0, 40.0, 30.0, None, 60.0, 16.6, "NOT_READY")
    assert e1["readiness_pct"] == 60.0 and e2["readiness_pct"] == 80.0 and e3["readiness_pct"] == 100.0
    assert e4["readiness_pct"] is None and "readiness_pct" in e4["unknown_fields"]
    print("OK — الجاهزية نسبة متدرجة: 36/60→60% ثم 48/60→80% ثم 60/60→100%، والمجهول معلَن")


async def main():
    tests = [test_agreement_up, test_conflict_flagged, test_no_valid_insufficient,
             test_contract_shape, test_context_units_not_voted, test_health_states,
             test_fast_card_eight_fields, test_slow_card_and_section_merge_55_45,
             test_missing_path_declared_not_hidden, test_path_weight_rebalance_q2,
             test_readiness_pct_gradual]
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
