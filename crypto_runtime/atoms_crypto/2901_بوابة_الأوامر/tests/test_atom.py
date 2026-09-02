import asyncio
import json
import os
import sqlite3
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom901", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom901"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_HALT_REQUEST = _mod.EVENT_HALT_REQUEST
EVENT_RESET = _mod.EVENT_RESET
EVENT_STATE = _mod.EVENT_STATE
EVENT_PARAMETER_APPROVE = _mod.EVENT_PARAMETER_APPROVE
EVENT_PARAMETER_STATE = _mod.EVENT_PARAMETER_STATE
EVENT_TILT_RULE = _mod.EVENT_TILT_RULE
EVENT_GATE_COMMAND = _mod.EVENT_GATE_COMMAND
ACTIONS = _mod.ACTIONS

import pytest  # noqa: E402


@pytest.fixture
def tmp(tmp_path):
    return str(tmp_path)


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
        return AtomContext(atom_id=901, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _insert(db_path, action, requested_at, operator="dashboard", payload=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS commands ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, "
        "operator TEXT NOT NULL, requested_at REAL NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'PENDING', executed_at REAL, "
        "payload_json TEXT)")
    conn.execute(
        "INSERT INTO commands (action, operator, requested_at, payload_json) "
        "VALUES (?, ?, ?, ?)",
        (action, operator, requested_at,
         None if payload is None else json.dumps(payload)))
    conn.commit()
    conn.close()


def _status_of(db_path, row_id):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM commands WHERE id = ?",
                       (row_id,)).fetchone()
    conn.close()
    return row[0] if row else None


async def _new(db_path):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": db_path, "max_age_s": 120, "batch_limit": 20}))
    await atom.start()
    return atom, bus


def _named(bus, name):
    return [p for n, p in bus.published if n == name]


async def test_reset_executed(tmp):
    print("\n--- test_reset_executed ---")
    db = os.path.join(tmp, "c1.db")
    _insert(db, "kill_switch_reset", 1000.0)
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1001.0})
    outs = _named(bus, EVENT_RESET)
    assert len(outs) == 1 and outs[0]["operator"] == "dashboard"
    assert _status_of(db, 1) == "DONE"
    await atom.stop()
    print("OK — أمر التصفير نُشر والصف DONE")


async def test_halt_executed(tmp):
    print("\n--- test_halt_executed ---")
    db = os.path.join(tmp, "c2.db")
    _insert(db, "halt", 1000.0)
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1001.0})
    assert len(_named(bus, EVENT_HALT_REQUEST)) == 1
    assert _status_of(db, 1) == "DONE"
    await atom.stop()
    print("OK — الإيقاف الطارئ نُشر")


async def test_stale_expired(tmp):
    print("\n--- test_stale_expired ---")
    db = os.path.join(tmp, "c3.db")
    _insert(db, "halt", 100.0)
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1000.0})
    assert len(_named(bus, EVENT_HALT_REQUEST)) == 0, "القديم لا يُنفَّذ"
    assert _status_of(db, 1) == "EXPIRED"
    await atom.stop()
    print("OK — أمر قديم → EXPIRED بلا تنفيذ")


async def test_unknown_rejected(tmp):
    print("\n--- test_unknown_rejected ---")
    db = os.path.join(tmp, "c4.db")
    _insert(db, "format_disk", 1000.0)
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1001.0})
    assert len(_named(bus, EVENT_HALT_REQUEST)) == 0
    assert len(_named(bus, EVENT_RESET)) == 0
    assert _status_of(db, 1) == "REJECTED"
    await atom.stop()
    print("OK — أمر غريب → REJECTED")


async def test_no_double_execution(tmp):
    print("\n--- test_no_double_execution ---")
    db = os.path.join(tmp, "c5.db")
    _insert(db, "kill_switch_reset", 1000.0)
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1001.0})
    await atom._on_pulse({"official_time": 1002.0})
    assert len(_named(bus, EVENT_RESET)) == 1, "الأمر لا يُنفَّذ مرّتين"
    await atom.stop()
    print("OK — نبضتان → تنفيذ واحد")


async def test_state_published(tmp):
    print("\n--- test_state_published ---")
    db = os.path.join(tmp, "c6.db")
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1000.0})
    states = _named(bus, EVENT_STATE)
    assert len(states) == 1 and states[0]["seen"] == 0
    _insert(db, "halt", 1000.0)
    await atom._on_pulse({"official_time": 1001.0})
    states = _named(bus, EVENT_STATE)
    assert states[-1]["executed_halt"] == 1
    await atom.stop()
    print("OK — الحالة تُنشر أول نبضة وبعد كل تنفيذ")


async def test_health(tmp):
    print("\n--- test_health ---")
    db = os.path.join(tmp, "c7.db")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": db, "max_age_s": 120, "batch_limit": 20}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    ready = await atom.health_check()
    assert ready.state == HealthState.HEALTHY and ready.message.startswith("READY")
    _insert(db, "kill_switch_reset", 1000.0)
    await atom._on_pulse({"official_time": 1001.0})
    assert (await atom.health_check()).state == HealthState.HEALTHY
    await atom.stop()
    print("OK — الصحة UNHEALTHY→HEALTHY(جاهز، صفر أمر)→HEALTHY(يعمل)")


class _temp_registry:
    """يوجّه سجلّ المُعامِلات إلى ملف مؤقت — القاعدة الحية ممنوعة بالاختبار."""

    def __init__(self, path):
        self.path = path
        self.previous = None

    def __enter__(self):
        self.previous = os.environ.get("QUANT_ANALYSIS_SETTINGS_DB")
        os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = self.path
        return self

    def __exit__(self, *_exc):
        if self.previous is None:
            os.environ.pop("QUANT_ANALYSIS_SETTINGS_DB", None)
        else:
            os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = self.previous
        return False


async def test_parameter_approve_executes_registry(tmp):
    print("\n--- test_parameter_approve_executes_registry ---")
    db = os.path.join(tmp, "c8.db")
    registry_db = os.path.join(tmp, "params_approve.db")
    _insert(db, "parameter_approve", 1000.0,
            payload={"name": "CONFIDENCE_BLEND", "value": 0.5})
    with _temp_registry(registry_db):
        atom, bus = await _new(db)
        await atom._on_pulse({"official_time": 1001.0})
        await atom.stop()
    commands = _named(bus, EVENT_PARAMETER_APPROVE)
    assert len(commands) == 1, "أمر الاعتماد يُنشر مرة واحدة"
    body = commands[0]
    assert body["name"] == "CONFIDENCE_BLEND" and body["value"] == 0.5
    assert body["operator"] == "dashboard" and body["origin"] == "901"
    assert body["command_id"] == 1 and body["command_requested_at"] == 1000.0
    states = _named(bus, EVENT_PARAMETER_STATE)
    assert states == [{"name": "CONFIDENCE_BLEND", "value": 0.5, "version": 1}]
    conn = sqlite3.connect(registry_db)
    row = conn.execute(
        "SELECT value, source, status, version, approved_by, approved_at "
        "FROM parameters WHERE name='CONFIDENCE_BLEND'").fetchone()
    audit = conn.execute(
        "SELECT command_id FROM parameters_audit WHERE name='CONFIDENCE_BLEND'"
    ).fetchall()
    conn.close()
    assert row == (0.5, "OWNER", "APPROVED", 1, "dashboard", 1001.0)
    assert audit == [("1",)], "سجل التدقيق يحمل هوية الأمر (idempotency)"
    assert _status_of(db, 1) == "DONE"
    print("OK — الأمر اعتمد فعلًا بالسجل المؤقت ونُشرت الحالة {name,value,version}")


async def test_parameter_approve_rejects_undeclared(tmp):
    print("\n--- test_parameter_approve_rejects_undeclared ---")
    db = os.path.join(tmp, "c9.db")
    registry_db = os.path.join(tmp, "params_reject.db")
    # اسم عيار قرار (له طريقه decision_setting) واسم مخترع وقيمة غير رقمية
    _insert(db, "parameter_approve", 1000.0,
            payload={"name": "DECISION_NEUTRAL_BAND", "value": 0.1})
    _insert(db, "parameter_approve", 1000.0,
            payload={"name": "INVENTED_PARAM", "value": 1.0})
    _insert(db, "parameter_approve", 1000.0,
            payload={"name": "MOVEMENT_FLOOR", "value": "abc"})
    with _temp_registry(registry_db):
        atom, bus = await _new(db)
        await atom._on_pulse({"official_time": 1001.0})
        await atom.stop()
    assert len(_named(bus, EVENT_PARAMETER_APPROVE)) == 0
    assert len(_named(bus, EVENT_PARAMETER_STATE)) == 0
    for row_id in (1, 2, 3):
        assert _status_of(db, row_id) == "REJECTED"
    assert not os.path.exists(registry_db), "الرفض لا يلمس السجل إطلاقًا"
    print("OK — عيار قرار/اسم مخترع/قيمة فاسدة → REJECTED بلا أي لمس للسجل")


async def test_tilt_rule_publishes_command(tmp):
    print("\n--- test_tilt_rule_publishes_command ---")
    db = os.path.join(tmp, "c10.db")
    # ث٣ (ق١٠ §١٨–٢١): قاعدة منحنى صالحة — عتبات تصاعدية، أرقام منتهية
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "confidence", "side": "up",
                     "points": [[80, 0.10], [85, 0.20]], "enabled": True})
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1001.0})
    await atom.stop()
    commands = _named(bus, EVENT_TILT_RULE)
    assert len(commands) == 1, "أمر قاعدة الترجيح يُنشر مرة واحدة"
    body = commands[0]
    assert body["field"] == "confidence" and body["side"] == "up"
    assert body["points"] == [[80.0, 0.10], [85.0, 0.20]], "النقاط مُطبَّعة أعدادًا عشرية"
    assert all(isinstance(n, float) for pair in body["points"] for n in pair)
    assert body["enabled"] is True
    assert body["operator"] == "dashboard" and body["origin"] == "901"
    assert body["command_id"] == 1 and body["command_requested_at"] == 1000.0
    # لا تطبيق داخل 901: المالك الوحيد للمخزن هو 580 — لا حالة تصدر من هنا
    assert len(_named(bus, "tilt.rules.state")) == 0
    assert len(_named(bus, "tilt.state")) == 0
    assert not os.path.exists(os.path.join(tmp, "tilt_rules.db")), \
        "901 لا يفتح مخزن قواعد الترجيح إطلاقًا"
    assert _status_of(db, 1) == "DONE"
    print("OK — tilt_rule نُشر بالحمولة الصحيحة بلا أي تطبيق داخل 901")


async def test_tilt_rule_allows_empty_and_abs(tmp):
    print("\n--- test_tilt_rule_allows_empty_and_abs ---")
    db = os.path.join(tmp, "c11.db")
    # مسح منحنى (نقاط فارغة) + جهة القيمة المطلقة + تعطيل — كلها شرعية
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "direction", "side": "abs",
                     "points": [], "enabled": False})
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1001.0})
    await atom.stop()
    commands = _named(bus, EVENT_TILT_RULE)
    assert len(commands) == 1
    assert commands[0]["points"] == [] and commands[0]["enabled"] is False
    assert _status_of(db, 1) == "DONE"
    print("OK — مسح المنحنى (نقاط فارغة) وside=abs وenabled=False تمرّ")


async def test_tilt_rule_rejects_invalid(tmp):
    print("\n--- test_tilt_rule_rejects_invalid ---")
    db = os.path.join(tmp, "c12.db")
    base = {"side": "up", "points": [[80, 0.1]], "enabled": True}
    # 1) state ممنوع (حاجز لا سلّم) · 2) weight ممنوع (عامل لا سلّم)
    _insert(db, "tilt_rule", 1000.0, payload={**base, "field": "state"})
    _insert(db, "tilt_rule", 1000.0, payload={**base, "field": "weight"})
    # 3) نقاط غير مرتبة تصاعديًّا · 4) عتبة مكرّرة (ليست تصاعدًا تامًّا)
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "confidence", "side": "up",
                     "points": [[85, 0.2], [80, 0.1]], "enabled": True})
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "confidence", "side": "up",
                     "points": [[80, 0.1], [80, 0.2]], "enabled": True})
    # 5) قيمة غير رقمية · 6) enabled غير منطقي · 7) جهة مخترعة
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "ratio", "side": "up",
                     "points": [[80, "abc"]], "enabled": True})
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "ratio", "side": "up",
                     "points": [[80, 0.1]], "enabled": "yes"})
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "ratio", "side": "sideways",
                     "points": [[80, 0.1]], "enabled": True})
    # 8) أكثر من 12 نقطة · 9) مفتاح غريب بالحمولة
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "strength", "side": "up",
                     "points": [[i, 0.01] for i in range(13)], "enabled": True})
    _insert(db, "tilt_rule", 1000.0,
            payload={"field": "strength", "side": "up", "points": [[80, 0.1]],
                     "enabled": True, "weight": 5})
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1001.0})
    await atom.stop()
    assert len(_named(bus, EVENT_TILT_RULE)) == 0, "لا أمر فاسد يمرّ"
    for row_id in range(1, 10):
        assert _status_of(db, row_id) == "REJECTED", f"الصف {row_id} يجب أن يُرفض"
    print("OK — state/weight/ترتيب فاسد/قيم فاسدة/جهة مخترعة/>12/مفتاح غريب → REJECTED")


async def test_gate_command_executes_and_closes_row(tmp):
    # سقط على الكود قبل الإصلاح: عدّاد execution_gate كان مفقودًا من _executed
    # فينفجر KeyError بعد النشر وقبل ختم الصفّ ⇒ الصفّ يبقى PENDING ويُعاد
    # التقاطه كل نبضة (مقيس حيًّا: 279 مرّة ٢٠٢٦-٠٨-٢٠ و120 ٢٠٢٦-٠٨-٢١).
    print("\n--- test_gate_command_executes_and_closes_row ---")
    db = os.path.join(tmp, "c13.db")
    _insert(db, "execution_gate", 1000.0, payload={"gate": "552", "enabled": True})
    atom, bus = await _new(db)
    await atom._on_pulse({"official_time": 1001.0})
    outs = _named(bus, EVENT_GATE_COMMAND)
    assert len(outs) == 1, "أمر البوّابة يجب أن يُنشر مرّة"
    assert _status_of(db, 1) == "DONE", "الصفّ يجب أن يُختم DONE لا أن يبقى PENDING"
    # ولا يُعاد التقاطه بنبضة ثانية
    await atom._on_pulse({"official_time": 1002.0})
    assert len(_named(bus, EVENT_GATE_COMMAND)) == 1, "لا إعادة إرسال"
    await atom.stop()
    print("OK — أمر البوّابة نُشر مرّة واحدة والصفّ خُتم DONE")


async def test_every_action_has_a_counter(tmp):
    # الجذر: _executed كان نسخة يدويّة من ACTIONS فافترقا. المصدر صار واحدًا.
    print("\n--- test_every_action_has_a_counter ---")
    db = os.path.join(tmp, "c14.db")
    atom, _ = await _new(db)
    missing = sorted(set(ACTIONS) - set(atom._executed))
    await atom.stop()
    assert not missing, f"أفعال بلا عدّاد: {missing}"
    print("OK — كل فعل بـACTIONS له عدّاد (%d فعلًا)" % len(ACTIONS))


async def main():
    tests = [test_reset_executed, test_halt_executed, test_stale_expired,
             test_unknown_rejected, test_no_double_execution,
             test_state_published, test_health,
             test_parameter_approve_executes_registry,
             test_parameter_approve_rejects_undeclared,
             test_tilt_rule_publishes_command,
             test_tilt_rule_allows_empty_and_abs,
             test_tilt_rule_rejects_invalid]
    failed = []
    # ignore_cleanup_errors: سجل المُعامِلات (sqlite WAL) يُغلق بالـGC لا يدويًّا،
    # وويندوز يرفض حذف ملف بمقبض حي — فشل التنظيف ليس فشل اختبار.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for t in tests:
            try:
                await t(tmp)
            except AssertionError as e:
                failed.append((t.__name__, str(e)))
                print(f"FAILED: {t.__name__}: {e}")
            except Exception as e:
                failed.append((t.__name__, repr(e)))
                print(f"ERROR: {t.__name__}: {e!r}")
        import gc
        gc.collect()  # يفكّ مقابض sqlite العالقة قبل محاولة حذف المجلد المؤقت
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
