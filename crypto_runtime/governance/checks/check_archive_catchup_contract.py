"""حارس عقد لحاق الأرشفة — حكم المالك ٢٠٢٦-٠٨-١٦.

> «لا تسجّل `last_success` عند بدء الأرشفة. فقط بعد اكتمال النسخ والتحقّق
>  والتأكّد أنّ المصدر لم يُفقد. وإذا فشلت في المنتصف، تبقى الحالة السابقة
>  كما هي، وبالتالي سيعيد النظام المحاولة عند الإقلاع التالي.»

المقيس الذي أنتج هذا العقد: `SYS_HOUR=0 · SYS_DAY=0` بعد ٤٤ دقيقة تشغيل.
`806` يبدأ المهلة من جديد بعد كل إقلاع، فالمهمّة اليوميّة تنتظر حدًّا لا يأتي.

حارس سلوكيّ: يشغّل آلة القرار نفسها، ويكسر كل شرط من المُدخَل.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
# الوحدة انتقلت من مجلد 714 إلى حزمة مشتركة بالجذر (تستخدمها 714/800/802/803).
spec = importlib.util.spec_from_file_location(
    "catchup", ROOT / "catchup" / "__init__.py")
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

bad = 0
checked = 0
DAY = C.DEFAULT_WINDOW_S
NOW = 1_786_800_000.0


def show(case: str, ok: bool, detail: str = "") -> None:
    global bad, checked
    checked += 1
    if not ok:
        bad += 1
    print("   %-58s %-24s %s" % (case, detail, "✓" if ok else "✘"))


print("=" * 106)
print("حارس عقد لحاق الأرشفة — الحالة تتقدّم بالتحقّق لا بالمحاولة")
print("=" * 106)

# ── ١· قرار الإقلاع ─────────────────────────────────────────────────────────
print("\n١· ماذا يقرّر النظام عند الإقلاع:")
v = C.decide({}, NOW)
show("لا سجلّ نجاح سابق ⇒ يشتغل فورًا",
     v["status"] == C.NEVER_RAN and v["run"] is True, v["reason"])

v = C.decide({"last_success": NOW - DAY - 1}, NOW)
show("مرّت النافذة ⇒ لحاق مطلوب",
     v["status"] == C.CATCHUP_REQUIRED and v["run"] is True,
     "عمر=%.0f ساعة" % (v["age_s"] / 3600))

v = C.decide({"last_success": NOW - 3600.0}, NOW)
show("داخل النافذة ⇒ لا تشغيل (لا أرشفة كل إقلاع)",
     v["status"] == C.SKIPPED and v["run"] is False, v["reason"])

v = C.decide({"last_success": NOW + 5000.0}, NOW)
show("ختم من المستقبل ⇒ لا يُصدَّق · يشتغل احتياطًا",
     v["status"] == C.CATCHUP_REQUIRED and v["run"] is True, v["reason"])

v = C.decide({"last_success": NOW - DAY - 1}, None)
show("بلا ساعة رسميّة ⇒ لا يبدأ عملًا لا يستطيع ختمه",
     v["status"] == C.SKIPPED and v["run"] is False, v["reason"])

for junk in ("أمس", float("nan"), [], {"a": 1}):
    v = C.decide({"last_success": junk}, NOW)
    show("ختم مشوّه (%s) ⇒ يُعامَل كأن لا سجلّ" % type(junk).__name__,
         v["status"] == C.NEVER_RAN and v["run"] is True, v["reason"])

# ── ٢· القاعدة الذهبيّة ─────────────────────────────────────────────────────
print("\n٢· `last_success` لا يُكتب إلّا بعد التحقّق الكامل:")
v = C.outcome(True, True, True, NOW, NOW + 12)
show("نُسخ · تُحقِّق · المصدر سليم ⇒ أُرشِف ويُسجَّل",
     v["status"] == C.ARCHIVED and v["persist_last_success"] is True
     and v["last_success"] == NOW + 12, "مدّة=%.0fث" % v["duration_s"])

for label, copied, verified, intact, reason in (
        ("نُسخ ولم يُتحقَّق", True, False, True, "NOT_VERIFIED"),
        ("لم يُنسخ أصلًا", False, True, True, "NOT_COPIED"),
        ("تُحقِّق لكنّ المصدر ضاع", True, True, False, "SOURCE_LOST"),
        ("نُسخ · تُحقِّق · والمصدر مجهول", True, True, None, "SOURCE_LOST")):
    v = C.outcome(copied, verified, intact, NOW, NOW + 12)
    show("%-38s ⇒ فشل ولا يُسجَّل" % label,
         v["status"] == C.FAILED and v["persist_last_success"] is False
         and v["reason"] == reason, v["reason"])

v = C.outcome(True, True, True, NOW, None)
show("نجح لكن بلا ختم انتهاء ⇒ لا يُسجَّل",
     v["status"] == C.FAILED and v["persist_last_success"] is False,
     v["reason"])

print("\n   والمصدر لا يُفقد في أيّ فشل:")
for copied, verified, intact in ((False, True, True), (True, False, True),
                                 (True, True, False), (True, True, None)):
    v = C.outcome(copied, verified, intact, NOW, NOW + 1)
    show("فشل بسبب %-22s ⇒ المصدر محفوظ" % v["reason"],
         v["source_kept"] is True, "kept=%s" % v["source_kept"])

# ── ٣· الفشل يعيد المحاولة عند الإقلاع التالي ──────────────────────────────
print("\n٣· دورة كاملة: فشل ثمّ إقلاع ⇒ يعيد المحاولة:")
state = {"last_success": NOW - DAY - 1}
first = C.decide(dict(state), NOW)
run = C.outcome(True, False, True, NOW, NOW + 5)          # فشل التحقّق
if run["persist_last_success"]:
    state["last_success"] = run["last_success"]
second = C.decide(dict(state), NOW + 60)
show("الفشل لم يقدّم الحالة",
     state["last_success"] == NOW - DAY - 1, "state=%.0f" % state["last_success"])
show("والإقلاع التالي ما زال يطلب اللحاق",
     first["run"] is True and second["run"] is True
     and second["status"] == C.CATCHUP_REQUIRED, second["status"])

state = {"last_success": NOW - DAY - 1}
run = C.outcome(True, True, True, NOW, NOW + 5)           # نجاح مُتحقَّق
if run["persist_last_success"]:
    state["last_success"] = run["last_success"]
third = C.decide(dict(state), NOW + 60)
show("والنجاح المُتحقَّق يقدّمها فيسكت النظام",
     third["run"] is False and third["status"] == C.SKIPPED, third["reason"])

print("\n" + "=" * 106)
print("الفحوص = %d · الاختلافات = %d" % (checked, bad))
print("سليم: اللحاق يقع عند الحاجة، ولا يُسجَّل نجاح لم يُتحقَّق."
      if bad == 0 else "ساقط: حالة تتقدّم بلا تحقّق أو لحاق لا يقع.")
sys.exit(1 if bad else 0)
