"""حارس عقد اللقطة الدائمة — حكم المالك ٢٠٢٦-٠٨-١٦.

> «الحالة لا تُعتبر محفوظة لمجرّد الكتابة: write → verify → commit.
>  والفشل لا يمسح الحالة السابقة. ولقطة جزئيّة ليست لقطة صالحة.»

والتفريق الذي أمر بحفظه:
    ختم الجلسة  ⇐ هل المعرّف فريد عبر الجلسات؟
    اللقطة      ⇐ هل أعرف أين كنت قبل إعادة التشغيل؟
واحد لا يستبدل الآخر — ويُفحص هنا أنّهما يظهران معًا.

السيناريو المطلوب حرفيًّا: حالة معروفة ← لقطة ← تحقّق ← إعادة تشغيل ←
استرجاع ← الحالة نفسها. ثمّ كسرها عمدًا وإثبات أنّها لا تُقبل حالةً رسميّة.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("snapshot_state",
                                              ROOT / "shared" / "snapshot_state.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

bad = 0
checked = 0


def show(case: str, ok: bool, detail: str = "") -> None:
    global bad, checked
    checked += 1
    if not ok:
        bad += 1
    print("   %-58s %-24s %s" % (case, detail, "✓" if ok else "✘"))


# حالة معروفة تجمع الثلاثة التي تعتمد على اللقطة.
KNOWN = {
    "pair": {"pair_id": "pair-A-BTCUSD-1786800000-7", "cycle_id": "BTCUSD|60s|9",
             "status": "COMPLETE", "legs": {"BUY": "RECONCILED",
                                            "SELL": "RECONCILED"}},
    "counters": {"pair_counter": 7, "request_counter": 42},
    "archive": {"last_success": 1786700000.0, "last_window": "2026-08-15"},
}


def snap(payload=None, epoch=1786800000, **over):
    body = KNOWN if payload is None else payload
    record = {"schema_version": 1, "written_at": 1786800001.0,
              "session_epoch": epoch, "payload": body,
              "digest": S.digest_of(body)}
    record.update(over)
    return record


print("=" * 108)
print("حارس عقد اللقطة الدائمة — الحالة تصير رسميّة بالتحقّق لا بالكتابة")
print("=" * 108)

# ── ١· السيناريو المطلوب ────────────────────────────────────────────────────
print("\n١· حالة معروفة ← لقطة ← تحقّق ← إعادة تشغيل ← استرجاع:")
written = snap()
v = S.grade(written)
show("اللقطة المكتوبة تُتحقَّق ⇒ صالحة", v["grade"] == S.VALID, v["grade"])

c = S.commit(written, None)
show("وتُعتمد حالةً رسميّة", c["commit"] is True and c["official"] is written,
     c["reason"])

r = S.resume(c["official"], live_epoch=1786900000)
show("وبعد إعادة التشغيل تُسترجَع الحالة نفسها",
     r["restored"] is True and r["state"] == KNOWN, r["reason"])
show("والعدّادات لا تعود صفرًا",
     r["counters_reset"] is False
     and r["state"]["counters"]["pair_counter"] == 7,
     "counter=%s" % r["state"]["counters"]["pair_counter"])
show("وحالة الزوج تستمرّ كما هي",
     r["state"]["pair"]["status"] == "COMPLETE"
     and r["state"]["pair"]["pair_id"] == KNOWN["pair"]["pair_id"], "COMPLETE")
show("وحالة الأرشفة تستمرّ فلا تُعاد كل إقلاع",
     r["state"]["archive"]["last_success"] == 1786700000.0,
     "last=%.0f" % r["state"]["archive"]["last_success"])
show("والختمان يظهران معًا — الهويّة والاستمراريّة مسألتان",
     r["previous_epoch"] == 1786800000 and r["live_epoch"] == 1786900000,
     "%s → %s" % (r["previous_epoch"], r["live_epoch"]))

# ── ٢· الكسر: لقطة ناقصة أو تالفة ──────────────────────────────────────────
print("\n٢· الكسر — ولا تخمين:")
for field in S.REQUIRED:
    broken = snap()
    broken[field] = None
    v = S.grade(broken)
    show("حقل ناقص (%s) ⇒ جزئيّة لا صالحة" % field,
         v["grade"] == S.PARTIAL and v["restore"] is False
         and field in v["missing"], v["grade"])

tampered = snap()
tampered["payload"] = {**KNOWN, "counters": {"pair_counter": 999}}
v = S.grade(tampered)
show("محتوى غُيّر بعد الختم ⇒ تالفة", v["grade"] == S.CORRUPT
     and v["restore"] is False, v["reason"])

for junk, label in ((None, "لا لقطة"), ("نصّ", "ليست سجلًّا"), ([], "قائمة")):
    v = S.grade(junk)
    show("%-22s ⇒ لا استرجاع ولا تداول" % label,
         v["restore"] is False and v["trade"] is False, v["grade"])

# ── ٣· الفشل لا يمسح السابقة ───────────────────────────────────────────────
print("\n٣· لقطة N تفشل ⇒ لقطة N−1 تبقى رسميّة:")
good = snap()
official = S.commit(good, None)["official"]
for label, candidate in (("ناقصة", snap(**{"digest": None})),
                         ("تالفة", {**snap(), "digest": "0" * 64}),
                         ("لا شيء", None),
                         ("ليست سجلًّا", "خربانة")):
    c = S.commit(candidate, official)
    show("مرشّحة %-12s ⇒ لا تُعتمد والسابقة تبقى" % label,
         c["commit"] is False and c["official"] is official
         and c["previous_kept"] is True, c["reason"])

# ── ٤· غير الصالحة تمنع التداول لا تخمّنه ──────────────────────────────────
print("\n٤· كل حالة غير VALID لها سياسة معلنة:")
for gr in (S.PARTIAL, S.CORRUPT, S.UNKNOWN):
    policy = S.POLICY[gr]
    show("%-8s ⇒ لا استرجاع ولا تداول" % gr,
         policy["restore"] is False and policy["trade"] is False,
         policy["reason"])
show("و VALID وحدها تسمح",
     S.POLICY[S.VALID]["restore"] is True and S.POLICY[S.VALID]["trade"] is True,
     "VERIFIED")

r = S.resume(None, live_epoch=1786900000)
show("وبلا لقطة صالحة: تُعلَن التصفير ولا تُقدَّم كقراءة حقيقيّة",
     r["restored"] is False and r["state"] is None
     and r["counters_reset"] is True and r["trade_allowed"] is False,
     r["reason"])

# ── ٥· اللقطة مصدر استمراريّة لا مصدر حقيقة ────────────────────────────────
print("\n٥· بعد الاسترجاع يجب التحقّق من الواقع — لا تصديق أعمى:")
legs = {"111": {"side": "BUY"}, "222": {"side": "SELL"}}

v = S.reconcile(legs, dict(legs))
show("اللقطة تطابق صورة الوسيط ⇒ تأكيد وتداول",
     v["verdict"] == S.CONFIRMED and v["trade_allowed"] is True
     and v["freeze_path"] is False, v["reason"])

v = S.reconcile(legs, {"111": {"side": "BUY"}})
show("اللقطة تقول رجل مفتوحة والوسيط لا يعرفها ⇒ تعارض",
     v["verdict"] == S.CONFLICT and v["freeze_path"] is True
     and v["only_in_snapshot"] == ["222"], "شبح=%s" % v["only_in_snapshot"])
show("ولا يُختار رابح من الذاكرة — يُجمَّد المسار",
     v["trade_allowed"] is False, "تداول=%s" % v["trade_allowed"])

v = S.reconcile(legs, {**legs, "333": {"side": "BUY"}})
show("مركز عند الوسيط لا نعرفه ⇒ تعارض أيضًا",
     v["verdict"] == S.CONFLICT and v["only_in_live"] == ["333"],
     "غريب=%s" % v["only_in_live"])

v = S.reconcile(legs, None)
show("صورة الوسيط لم تصل بعد ⇒ لا يُقرأ اتّفاقًا",
     v["verdict"] == S.NO_EVIDENCE and v["freeze_path"] is True
     and v["trade_allowed"] is False, v["reason"])

v = S.reconcile({}, {})
show("لا ذاكرة ولا مراكز ⇒ اتّفاق حقيقيّ",
     v["verdict"] == S.CONFIRMED, v["reason"])

v = S.reconcile(legs, {})
show("الذاكرة تحمل مركزين والوسيط فارغ ⇒ تعارض لا تصفير",
     v["verdict"] == S.CONFLICT and len(v["only_in_snapshot"]) == 2,
     "أشباح=%d" % len(v["only_in_snapshot"]))

print("\n" + "=" * 108)
print("الفحوص = %d · الاختلافات = %d" % (checked, bad))
print("سليم: لا حالة رسميّة بلا تحقّق، ولا فشلٍ يمحو ما قبله."
      if bad == 0 else "ساقط: حالة تُقبل بلا تحقّق أو فشل يمحو السابقة.")
sys.exit(1 if bad else 0)
