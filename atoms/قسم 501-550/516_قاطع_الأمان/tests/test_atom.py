# -*- coding: utf-8 -*-
"""اختبارات 516 — قاطع الأمان (سلطة المخاطر لكل حساب).

مُعاد هيكلتها من سطر واحد مضغوط (تعذّر تشخيص أيّ تأكيد فشل بلا نصّ الخطأ
كاملًا) إلى دوال مسمّاة قصيرة — نفس السيناريوهات، تشخيص ممكن.
"""
import asyncio
import sys
import tempfile
import threading
import time
from pathlib import Path as _Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "_atom516", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom516"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

from core.contracts.atom import AtomContext  # noqa: E402


class _NullLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class FakeEventBus:
    def __init__(self):
        self.published = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))


async def _new(db_path):
    bus = FakeEventBus()
    atom = Atom()
    cfg = {"max_daily_loss_pct": 5, "max_consecutive_losses": 3,
           "max_daily_trades": 20, "max_open_trades": 5,
           "consumer_db_path": str(db_path)}
    await atom.initialize(AtomContext(atom_id=516, config=cfg, logger=_NullLogger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    return atom, bus


async def _seed_account(atom, account="A", broker="BR", equity=1000.0):
    """يطابق تسلسل الأحداث الحقيقي: هوية ثم رصيد ثم طرفية جاهزة."""
    await atom._on_truth_equity({"account_id": account, "broker": broker, "equity": equity})
    await atom._on_account({"account_id": account, "broker": broker})
    await atom._on_terminal({"account_id": account, "connected": True,
                             "trade_allowed": True, "expert_allowed": True})


def _events(bus, name):
    return [p for n, p in bus.published if n == name]


async def test_1_validation_reserves_and_rejects_budget_excess(tmp_path):
    print("\n--- (١) الحجز يتراكم والتحقّق يرفض تجاوز الميزانية ---")
    atom, bus = await _new(tmp_path / "t1.db")
    await _seed_account(atom)
    await atom._on_ledger({"ledgers": [{"account_id": "A", "broker": "BR",
                                        "symbol": "NQ", "R": 100}]})
    await atom._on_validate({"account_id": "A", "broker": "BR", "request_id": "r1",
                             "symbol": "NQ", "action": "OPEN", "risk_budget": 60})
    await atom._on_validate({"account_id": "A", "broker": "BR", "request_id": "r2",
                             "symbol": "NQ", "action": "OPEN", "risk_budget": 60})
    rows = _events(bus, _mod.EVENT_VALIDATED)
    assert rows[-2]["approved"] is True, rows[-2]
    assert rows[-1]["approved"] is False and rows[-1]["reason"] == "RISK_BUDGET_EXCEEDED", rows[-1]
    print("OK — أوّل حجز (60) يمرّ؛ ثانٍ (60 فوق ميزانية 100) يُرفض RISK_BUDGET_EXCEEDED")


async def test_2_incomplete_loss_still_counts_and_can_trip(tmp_path):
    print("\n--- (٢) خسارة بتكلفة ناقصة تُحسَب فعلاً وقد تُسقِط القاطع (v5.1.0) ---")
    atom, bus = await _new(tmp_path / "t2.db")
    await _seed_account(atom)
    before = dict(atom.book("A"))
    # قبل v5.1.0 كانت التكلفة الناقصة تُسقِط الخسارة بصمت (باب فاشل-مفتوح).
    # الإصلاح الموثَّق بـatom.py: أيّ رقم خسارة وصل يُحسَب، ولو كانت
    # تكلفته ناقصة -- فقط غياب الرقم تمامًا يُسقَط.
    await atom._on_loss({"event_id": "loss:incomplete", "account_id": "A",
                         "completeness": "INCOMPLETE", "loss_pct": 99, "is_loss": True})
    after = atom.book("A")
    assert after != before, "خسارة بتكلفة ناقصة يجب أن تُغيّر الدفتر -- لم يعد يُسقَط بصمت"
    assert abs(after["daily_loss_pct"] - 99.0) < 1e-9, after
    assert after["incomplete_costs"] == 1, after
    assert after["kill"] is True and after["reason"] == "RISK_DAILY_LIMIT", after
    print("OK — 99%% ناقصة التكلفة: حُسبت (daily_loss_pct=99) وأسقطت القاطع فعلاً")


async def test_3_loss_with_no_number_at_all_is_ignored(tmp_path):
    print("\n--- (٣) خسارة بلا رقم إطلاقًا (لا حتى ناقص) تُسقَط وحدها ---")
    atom, bus = await _new(tmp_path / "t3.db")
    await _seed_account(atom)
    before = dict(atom.book("A"))
    await atom._on_loss({"event_id": "loss:no-number", "account_id": "A"})
    assert atom.book("A") == before, "بلا loss_pct إطلاقًا: لا تغيير -- الحالة الوحيدة المُسقَطة"
    assert atom._incomplete_ignored == 1
    print("OK — غياب الرقم تمامًا (لا قيمة ناقصة) هو الحالة الوحيدة المُتجاهَلة")


async def test_4_day_roll_resets_counters_not_kill(tmp_path):
    print("\n--- (٤) تصفير اليوم يُصفّر العدّادات ولا يفكّ القاطع ---")
    atom, bus = await _new(tmp_path / "t4.db")
    await _seed_account(atom)
    await atom._on_loss({"event_id": "loss:trip", "account_id": "A",
                         "completeness": "COMPLETE", "loss_pct": 6, "is_loss": True})
    assert atom.book("A")["kill"] is True
    await atom._on_day({"pulse_id": "SYS_DAY|1", "bucket_start": 1})
    b = atom.book("A")
    assert b["kill"] is True, "تصفير اليوم ليس تحريرًا -- القاطع يبقى مقفولًا"
    assert b["daily_loss_pct"] == 0.0 and b["daily_trade_count"] == 0
    print("OK — بعد التصفير: العدّادات صفر والقاطع لا يزال مقفولًا")


async def test_5_owner_reset_clears_kill(tmp_path):
    print("\n--- (٥) رفع المالك الصريح يفكّ القاطع ---")
    atom, bus = await _new(tmp_path / "t5.db")
    await _seed_account(atom)
    await atom._on_loss({"event_id": "loss:trip", "account_id": "A",
                         "completeness": "COMPLETE", "loss_pct": 6, "is_loss": True})
    assert atom.book("A")["kill"] is True
    await atom._on_reset({"account_id": "A"})
    assert atom.book("A")["kill"] is False
    print("OK — رفع صريح بحساب محدَّد يفكّ قاطعه")


async def test_6_snapshot_restore_roundtrip(tmp_path):
    print("\n--- (٦) اللقطة والاستعادة تحافظان على الدفاتر ---")
    atom, bus = await _new(tmp_path / "t6.db")
    await _seed_account(atom)
    snap = await atom.snapshot()
    restored = Atom()
    await restored.restore(snap)
    assert "A" in restored._books
    assert restored.book("A")["broker"] == "BR"
    print("OK — الدفاتر نجت من لقطة/استعادة كاملة")


async def test_7_concurrent_loss_and_trip_kill_flag_not_lost(tmp_path):
    print("\n--- (٧) سباق: خسارة قيد الحفظ + قطع يدوي متزامن -- القاطع لا يضيع ---")
    atom, bus = await _new(tmp_path / "t7.db")
    await _seed_account(atom)
    release = threading.Event()

    def fake_reduce(identity, account_id, event_type, source_identity,
                    source_payload, consumer, scope_key, initial_state, reducer):
        # يحاكي استعلامًا بدأ ورأى kill=False (كحال الحساب قبل أيّ قطع)
        # لكنّه لا "يُنجَز" (لا يُطبَّق على الذاكرة عبر b.update) إلا بعد أن
        # يُطلقه الاختبار صراحةً -- بعد أن يكون _trip قد ضبط kill=True
        # بالذاكرة فعلاً. بلا قفل: هذا يعني أنّ عودة الخسارة المتأخّرة تكتب
        # state القديم (kill=False) فوق قطع _trip الحيّ. مع القفل: _trip
        # لا يستطيع حتى قراءة b["kill"] إلى أن تُفرِج الخسارة عن القفل.
        release.wait(timeout=2.0)
        new_state, outputs = reducer({"daily_loss_pct": 0.0, "consecutive_losses": 0,
                                      "daily_trade_count": 0, "kill": False, "reason": ""})
        return True, new_state

    atom._journal.reduce_consumer_event = fake_reduce
    loss_task = asyncio.create_task(atom._on_loss(
        {"event_id": "loss:small", "account_id": "A", "completeness": "COMPLETE",
         "loss_pct": 1.0, "is_loss": True}))
    await asyncio.sleep(0.01)
    trip_task = asyncio.create_task(atom._trip("A", "MANUAL_HALT"))
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.gather(loss_task, trip_task)
    b = atom.book("A")
    assert b["kill"] is True, ("القاطع ضاع بالسباق -- كانت هذه الحالة قبل"
                               " القفل لكل حساب: %r" % b)
    print("OK — القفل لكل حساب يمنع خسارة متأخّرة من إطفاء قطع حيّ")


async def test_8_persist_write_off_loop_thread(tmp_path):
    print("\n--- (٨) كتابة القطع لا تجمّد حلقة الحدث (asyncio.to_thread فعليًّا) ---")
    atom, bus = await _new(tmp_path / "t8.db")
    await _seed_account(atom)
    real_save = atom._journal.save_consumer_state

    def slow_save(*args, **kwargs):
        time.sleep(0.3)
        return real_save(*args, **kwargs)

    atom._journal.save_consumer_state = slow_save
    order = []

    async def other_task():
        await asyncio.sleep(0.05)
        order.append("other_task")

    async def trip_call():
        await atom._trip("A", "MANUAL_HALT")
        order.append("trip_call")

    await asyncio.gather(other_task(), trip_call())
    assert order == ["other_task", "trip_call"], order
    print("OK — حلقة الحدث بقيت حرّة أثناء كتابة القطع (fsync=FULL بطيء لا يجمّدها)")


async def main():
    tests = [test_1_validation_reserves_and_rejects_budget_excess,
             test_2_incomplete_loss_still_counts_and_can_trip,
             test_3_loss_with_no_number_at_all_is_ignored,
             test_4_day_roll_resets_counters_not_kill,
             test_5_owner_reset_clears_kill,
             test_6_snapshot_restore_roundtrip,
             test_7_concurrent_loss_and_trip_kill_flag_not_lost,
             test_8_persist_write_off_loop_thread]
    failed = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        tmp_path = _Path(tmp_dir)
        for test in tests:
            try:
                await test(tmp_path)
            except AssertionError as e:
                failed.append((test.__name__, str(e)))
                print(f"FAILED: {test.__name__}: {e}")
            except Exception as e:
                failed.append((test.__name__, repr(e)))
                print(f"ERROR: {test.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
