import asyncio
import inspect
import json
import os
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
from pathlib import Path as _AtomPath  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom001", _AtomPath(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom001"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_PRESENCE = _mod.EVENT_PRESENCE
EVENT_INTEGRITY_ALERT = _mod.EVENT_INTEGRITY_ALERT


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
        self._all_handlers = []

    def subscribe(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)

    def subscribe_all(self, handler):
        self._all_handlers.append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))
        for handler in self._handlers.get(name, []):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result
        for handler in self._all_handlers:
            result = handler(name, payload)
            if inspect.isawaitable(result):
                await result

    def make_context(self, config, with_eyes=False):
        kwargs = dict(atom_id=1, config=config, logger=_NullLogger(),
                      publish=self.publish, subscribe=self.subscribe)
        if with_eyes:
            kwargs["subscribe_all"] = self.subscribe_all
        return AtomContext(**kwargs)


def _write_lock(path, digest, version="1.13.0", sealed_at="2026-08-18T14:57:30+00:00"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"core_version": version, "sealed_at": sealed_at,
                   "algorithm": "sha256", "root_digest": digest,
                   "file_count": 23}, f)


async def _make_atom(lock_path):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"core_lock_path": lock_path}))
    await atom.start()
    return bus, atom


def _alerts(bus):
    return [p for n, p in bus.published if n == EVENT_INTEGRITY_ALERT]


async def test_first_pulse_establishes_baseline_and_announces_it():
    print("\n--- test_first_pulse_establishes_baseline_and_announces_it ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111")
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})

        alerts = _alerts(bus)
        assert len(alerts) == 1, "خط الأساس لازم يُعلن مرّة، لا يُخفى بالصمت"
        assert alerts[0]["baseline_established"] is True
        assert alerts[0]["old_digest"] is None
        assert alerts[0]["new_digest"] == "aaa111"
        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY
        assert atom._alerts_raised == 0, "إعلان الأساس مو نفس عدّاد التنبيهات (لا تسلّل/كسر)"
        print("OK — أول نبضة أعلنت خط الأساس، ما خبّته بصمت")


async def test_detects_digest_change_and_alerts():
    print("\n--- test_detects_digest_change_and_alerts ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111", version="1.13.0")
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})  # baseline

        _write_lock(lock_path, "bbb222", version="1.14.0")
        await bus.publish(_mod.EVENT_PULSE, {"official_time": 123.0})

        alerts = [a for a in _alerts(bus) if not a["baseline_established"]]
        assert len(alerts) == 1, "لازم ينشر تنبيه واحد بالضبط عند التغيّر"
        alert = alerts[0]
        assert alert["old_digest"] == "aaa111"
        assert alert["new_digest"] == "bbb222"
        assert alert["old_version"] == "1.13.0"
        assert alert["new_version"] == "1.14.0"
        assert atom._alerts_raised == 1
        h = await atom.health_check()
        assert h.state == HealthState.DEGRADED
        print(f"OK — رُصد التغيّر ونُشر التنبيه: {alert['old_digest']} -> {alert['new_digest']}")


async def test_version_only_change_is_detected_even_with_same_digest():
    print("\n--- test_version_only_change_is_detected_even_with_same_digest ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111", version="1.13.0", sealed_at="T1")
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})  # baseline

        # نفس البصمة بالضبط، بس رقم نسخة ووقت ختم مختلفين — بالضبط سيناريو
        # ختم جزئي/تلاعب مباشر بالحقول بدون تغيير البصمة.
        _write_lock(lock_path, "aaa111", version="1.14.0", sealed_at="T2")
        await bus.publish(_mod.EVENT_PULSE, {})

        alerts = [a for a in _alerts(bus) if not a["baseline_established"]]
        assert len(alerts) == 1, "تغيّر النسخة/وقت الختم لازم يُكتشف حتى لو البصمة ثابتة"
        assert alerts[0]["old_version"] == "1.13.0"
        assert alerts[0]["new_version"] == "1.14.0"
        h = await atom.health_check()
        assert h.state == HealthState.DEGRADED
        assert "core_version" in h.message
        print("OK — تغيّر النسخة وحده (بصمة ثابتة) انرصد وما مرّ بصمت")


async def test_no_alert_when_unchanged():
    print("\n--- test_no_alert_when_unchanged ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111")
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})  # baseline
        await bus.publish(_mod.EVENT_PULSE, {})  # same content again

        alerts = [a for a in _alerts(bus) if not a["baseline_established"]]
        assert len(alerts) == 0, "ما في تغيّر، ما في تنبيه"
        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY
        print("OK — بصمة ثابتة عبر نبضتين، صفر تنبيه كاذب")


async def test_unreadable_lock_reports_unknown_without_crash():
    print("\n--- test_unreadable_lock_reports_unknown_without_crash ---")
    bus, atom = await _make_atom(os.path.join("Z:\\", "no_such_lock_xyz.json"))
    await bus.publish(_mod.EVENT_PULSE, {})
    h = await atom.health_check()
    assert h.state == HealthState.UNKNOWN, "مسار غير مقروء = UNKNOWN بلا انهيار"
    assert len(_alerts(bus)) == 0, "لا يمكن مقارنة شيء غير مقروء — لا تنبيه كاذب"
    print(f"OK — مسار غير موجود: UNKNOWN بلا انهيار ({h.message[:40]}...)")


async def test_invalid_json_reports_unknown_without_crash():
    print("\n--- test_invalid_json_reports_unknown_without_crash ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("{not valid json::")
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})
        h = await atom.health_check()
        assert h.state == HealthState.UNKNOWN, "JSON تالف = UNKNOWN بلا انهيار"
        assert len(_alerts(bus)) == 0
        print("OK — JSON تالف (كتابة منقطعة): UNKNOWN بلا انهيار")


async def test_missing_root_digest_key_reports_unknown():
    print("\n--- test_missing_root_digest_key_reports_unknown ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump({"core_version": "1.13.0"}, f)  # بلا root_digest إطلاقاً
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})
        h = await atom.health_check()
        assert h.state == HealthState.UNKNOWN, "JSON صالح بلا root_digest = UNKNOWN، لا يُعتمد كخط أساس"
        assert len(_alerts(bus)) == 0
        print("OK — JSON صالح بلا root_digest: UNKNOWN، ما اتّخذ كخط أساس")


async def test_non_object_json_reports_unknown():
    print("\n--- test_non_object_json_reports_unknown ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})
        h = await atom.health_check()
        assert h.state == HealthState.UNKNOWN
        assert len(_alerts(bus)) == 0
        print("OK — JSON صحيح لكن مو كائن (قائمة): UNKNOWN بلا انهيار")


async def test_health_check_before_any_pulse_is_not_sampled():
    print("\n--- test_health_check_before_any_pulse_is_not_sampled ---")
    bus, atom = await _make_atom("does-not-matter.json")
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED
    assert h.message == "NOT_SAMPLED"
    print("OK — قبل أي نبضة: DEGRADED/NOT_SAMPLED، لا صحّة مصطنعة")


async def test_snapshot_restore_before_any_pulse_establishes_clean_baseline():
    print("\n--- test_snapshot_restore_before_any_pulse_establishes_clean_baseline ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "zzz999")
        _bus1, atom1 = await _make_atom(lock_path)
        snap = await atom1.snapshot()
        assert snap["last_known_digest"] is None, "قبل أي نبضة، ما في خط أساس محفوظ"

        bus2, atom2 = await _make_atom(lock_path)
        await atom2.restore(snap)
        await bus2.publish(_mod.EVENT_PULSE, {})

        alerts = _alerts(bus2)
        assert len(alerts) == 1 and alerts[0]["baseline_established"] is True, (
            "أول نبضة حقيقية بعد استعادة لقطة (بلا خط أساس) لازم تؤسس بنظافة، لا تنبيه كاذب")
        h = await atom2.health_check()
        assert h.state == HealthState.HEALTHY
        print("OK — لقطة قبل أي نبضة رجعت خط أساس نظيف، أول نبضة حقيقية أسسته صح")


async def test_restore_rejects_invalid_state_shapes():
    print("\n--- test_restore_rejects_invalid_state_shapes ---")
    bad_states = [
        ("غير قاموس", ["not", "a", "dict"]),
        ("بصمة برقم لا نص", {"last_known_digest": 5}),
        ("نسخة core برقم لا نص", {"last_known_version": 5}),
        ("وقت ختم برقم لا نص", {"last_known_sealed_at": 5}),
        ("عدّاد تنبيهات نص لا رقم", {"alerts_raised": "7"}),
        ("عدّاد تنبيهات سالب", {"alerts_raised": -1}),
        ("آخر تنبيه ليس قاموس", {"last_alert": "oops"}),
    ]
    for label, bad in bad_states:
        atom = Atom()
        raised = False
        try:
            await atom.restore(bad)
        except ValueError as e:
            raised = str(e) == "INVALID_NQ_STATE"
        assert raised, f"حالة فاسدة ({label}) لازم ترفض بوضوح، لا تُقبل بصمت"
    print(f"OK — كل الحالات الفاسدة ({len(bad_states)}) رُفضت صراحة، صفر تسلّل صامت")


async def test_multiple_sequential_changes_increment_counter_and_chain_correctly():
    print("\n--- test_multiple_sequential_changes_increment_counter_and_chain_correctly ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111")
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})  # baseline

        _write_lock(lock_path, "bbb222")
        await bus.publish(_mod.EVENT_PULSE, {})
        _write_lock(lock_path, "ccc333")
        await bus.publish(_mod.EVENT_PULSE, {})

        alerts = [a for a in _alerts(bus) if not a["baseline_established"]]
        assert len(alerts) == 2
        assert atom._alerts_raised == 2, "العدّاد لازم يتزايد مع كل تغيّر حقيقي، لا يتجمّد عند ١"
        assert alerts[0]["old_digest"] == "aaa111" and alerts[0]["new_digest"] == "bbb222"
        assert alerts[1]["old_digest"] == "bbb222" and alerts[1]["new_digest"] == "ccc333", (
            "التنبيه الثاني لازم يقارن بآخر حالة معروفة (bbb222)، لا بخط الأساس القديم المتجمّد")
        print(f"OK — تنبيهان متتاليان، العدّاد={atom._alerts_raised}، السلسلة صحيحة")


async def test_snapshot_restore_preserves_last_known_digest():
    print("\n--- test_snapshot_restore_preserves_last_known_digest ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111", version="1.13.0")
        bus1, atom1 = await _make_atom(lock_path)
        await bus1.publish(_mod.EVENT_PULSE, {})  # baseline established
        snap = await atom1.snapshot()
        assert snap["last_known_digest"] == "aaa111"

        # "restart": ذرّة جديدة تستعيد الحالة، بلا أي رصد سابق بذاكرتها
        bus2, atom2 = await _make_atom(lock_path)
        await atom2.restore(snap)

        _write_lock(lock_path, "ccc333", version="1.15.0")
        await bus2.publish(_mod.EVENT_PULSE, {})

        alerts = [a for a in _alerts(bus2) if not a["baseline_established"]]
        assert len(alerts) == 1
        assert alerts[0]["old_digest"] == "aaa111", "لازم تتذكر آخر بصمة معروفة عبر الاستعادة"
        assert alerts[0]["new_digest"] == "ccc333"
        print("OK — restart/restore حافظ على آخر بصمة معروفة، والتنبيه صار صح بعده")


async def test_presence_event_published_every_pulse():
    print("\n--- test_presence_event_published_every_pulse ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111")
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})
        await bus.publish(_mod.EVENT_PULSE, {})

        presence = [p for n, p in bus.published if n == EVENT_PRESENCE]
        assert len(presence) == 2, "حدث NQ ينشر كل نبضة، بغضّ النظر عن المطابقة"
        print(f"OK — حدث {EVENT_PRESENCE} انتشر مرتين مع نبضتين")


async def test_eyes_receive_every_event_when_core_offers_subscribe_all():
    print("\n--- test_eyes_receive_every_event_when_core_offers_subscribe_all ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111")
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context({"core_lock_path": lock_path},
                                               with_eyes=True))
        await atom.start()

        await bus.publish("some.unrelated.event", {"x": 1})
        await bus.publish("another.event.nobody.named", {"y": 2})

        h = await atom.health_check()
        assert h.details["eyes_active"] is True
        assert h.details["events_seen_total"] == 2, "لازم توصل كل الأحداث بلا تسمية"
        assert h.details["distinct_event_names"] == 2
        assert h.details["last_event_name"] == "another.event.nobody.named"
        print("OK — العيون شافت كل حدث بلا ما تسمّي ولا واحد")


async def test_eyes_absent_on_old_core_keeps_watchdog_alive():
    print("\n--- test_eyes_absent_on_old_core_keeps_watchdog_alive ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111")
        # سياق بلا subscribe_all (نواة أقدم من 1.14.0) — الذرّة لازم تشتغل
        # مراقبة ختم كاملة وتعلن غياب العيون بصدق، لا أن تنهار.
        bus, atom = await _make_atom(lock_path)
        await bus.publish(_mod.EVENT_PULSE, {})

        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY, "مراقبة الختم لازم تبقى حيّة بلا عيون"
        assert h.details["eyes_active"] is False
        assert h.details["events_seen_total"] == 0
        print("OK — على نواة قديمة: مراقبة الختم شغّالة والعيون معلنة غائبة بصدق")


async def test_eyes_distinct_names_capped_against_unbounded_memory():
    print("\n--- test_eyes_distinct_names_capped_against_unbounded_memory ---")
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = os.path.join(tmp, "CORE.lock")
        _write_lock(lock_path, "aaa111")
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context({"core_lock_path": lock_path},
                                               with_eyes=True))
        await atom.start()

        total = _mod._DISTINCT_NAMES_CAP + 5
        for i in range(total):
            await bus.publish(f"generated.event.{i}", {})

        h = await atom.health_check()
        assert h.details["events_seen_total"] == total, "العدّاد الكلي بلا سقف"
        assert h.details["distinct_event_names"] == _mod._DISTINCT_NAMES_CAP, \
            "قائمة الأسماء المميّزة لازم توقف عند السقف — لا نموّ ذاكرة بلا حدّ"
        assert h.details["distinct_names_capped"] is True
        print("OK — العدّاد كامل والأسماء المميّزة واقفة عالسقف معلنة")


async def main():
    tests = [
        test_eyes_receive_every_event_when_core_offers_subscribe_all,
        test_eyes_absent_on_old_core_keeps_watchdog_alive,
        test_eyes_distinct_names_capped_against_unbounded_memory,
        test_first_pulse_establishes_baseline_and_announces_it,
        test_detects_digest_change_and_alerts,
        test_version_only_change_is_detected_even_with_same_digest,
        test_no_alert_when_unchanged,
        test_unreadable_lock_reports_unknown_without_crash,
        test_invalid_json_reports_unknown_without_crash,
        test_missing_root_digest_key_reports_unknown,
        test_non_object_json_reports_unknown,
        test_health_check_before_any_pulse_is_not_sampled,
        test_snapshot_restore_before_any_pulse_establishes_clean_baseline,
        test_restore_rejects_invalid_state_shapes,
        test_multiple_sequential_changes_increment_counter_and_chain_correctly,
        test_snapshot_restore_preserves_last_known_digest,
        test_presence_event_published_every_pulse,
    ]
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
