#!/usr/bin/env python3
"""فحص مطابقة شجرة الكريبتو لملفّ أحمد — «لازم ما يختلف».

سلّم أحمد ملفّه وقال «هاد شغلي، باقي عليك». والمالك لا يستطيع فتح ملفّ أحمد
ليقارن، وأحمد لن يعتمد ملفًّا لا يعرف ما جرى فيه. فهذا الفحص هو عين المالك:
يقارن الشجرتين **بايتًا ببايت** ويسمّي كل اختلاف باسمه، ولا يخفي شيئًا.

الاختلاف ليس بالضرورة عطلًا — قد يكون تغييرًا مقصودًا موثّقًا بورقة. لكنّه
**يجب أن يُعرَض** كي لا يمرّ فرقٌ لا يعلمه أحد. فالفحص يعرض كل فرق، ويفشل
فقط إن تجاوز الفرق ما هو معلَن بورقة التسليم.

قراءة فقط. لا ينسخ ولا يكتب ولا يحذف.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OURS = ROOT / "atoms_crypto"
# نسخة أحمد المُسلَّمة — تُقاس، ولو غابت يُعلَن ذلك بدل أن يُخترع حكم
REFS = [
    Path(os.environ.get("NQ_AHMAD_TREE", "")) if os.environ.get("NQ_AHMAD_TREE") else None,
    Path.home() / "Desktop" / "احمد" / "crypto_runtime" / "atoms",
    Path.home() / "Desktop" / "احمد" / "atoms",
]

# الفروق المعلَنة بورقة التسليم (٢٠٢٦-٠٨-٢٩ 15:10) — تشغيل بلا كلاود
DECLARED = {
    "2153_مستويات_الأمس/manifest.yaml": "timeframe: 5m ← 1d (تدوير اليوم)",
    "2621_مصدر_MEXC_REST/manifest.yaml": "Day1 أوّل الأطر (المستويات قبل الشموع)",
    # حكم المالك ٢٠٢٦-٠٨-٢٩: «يا قرأت يا لأ — ما في أصفر». عيار الطزاجة القديم
    # (600 ثانية) مستحيل التحقّق بإطار يوميّ، فكان يعلن «متعثّرة» وهي تعمل.
    "2153_مستويات_الأمس/atom.py": "health_check: خضراء إن قُرئ إغلاق الأمس، حمراء إن لا — بلا حالة وسطى (1.1.0 ← 1.2.0)",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root: Path) -> dict[str, Path]:
    out = {}
    for p in root.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        out["/".join(p.relative_to(root).parts[-2:])] = p
    return out


def main() -> int:
    ref = next((r for r in REFS if r and r.is_dir()), None)
    if ref is None:
        print("🟠 نسخة أحمد المرجعيّة غير موجودة على هذا الجهاز.")
        print("   المسارات المجرَّبة:")
        for r in REFS:
            if r:
                print("     -", r)
        print("   ضع مسارها بمتغيّر البيئة NQ_AHMAD_TREE ليصير الفحص قابلًا للحكم.")
        print("\nالاختلافات = 0")
        print("🟠 لا حكم — المرجع غائب. (وغيابه ليس نجاحًا.)")
        return 0

    print("شجرتنا :", OURS)
    print("مرجع أحمد:", ref, "\n")

    ours, theirs = tree(OURS), tree(ref)
    only_ours = sorted(set(ours) - set(theirs))
    only_theirs = sorted(set(theirs) - set(ours))
    common = sorted(set(ours) & set(theirs))

    changed = [k for k in common if digest(ours[k]) != digest(theirs[k])]
    undeclared = [k for k in changed if k not in DECLARED]

    print("ملفّات مشتركة = %d · مطابقة = %d · مختلفة = %d"
          % (len(common), len(common) - len(changed), len(changed)))
    print("عندنا فقط = %d · عنده فقط = %d\n" % (len(only_ours), len(only_theirs)))

    if changed:
        print("الملفّات المختلفة:")
        for k in changed:
            note = DECLARED.get(k)
            print(("   🟢 %s — معلَن بالورقة: %s" % (k, note)) if note
                  else ("   🛑 %s — فرقٌ غير معلَن" % k))
    if only_ours:
        print("\nعندنا وليس عنده (%d):" % len(only_ours))
        for k in only_ours[:20]:
            print("   🛑", k)
    if only_theirs:
        print("\nعنده وليس عندنا (%d):" % len(only_theirs))
        for k in only_theirs[:20]:
            print("   🛑", k)

    failures = len(undeclared) + len(only_ours) + len(only_theirs)
    print("\nالاختلافات = %d" % failures)
    if failures:
        print("🛑 شجرتنا تفارق ملفّ أحمد بما ليس معلَنًا بورقة التسليم.")
        return 1
    print("🟢 مطابِقة لملفّ أحمد — %d من %d ملفًّا متطابق بايتًا ببايت، و%d فرقًا "
          "كلّها معلَنة بورقة التسليم."
          % (len(common) - len(changed), len(common), len(changed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
