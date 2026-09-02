"""الحالة المرجعيّة المجمَّدة قبل حزمة الإغلاق — حكم المالك ٢٠٢٦-٠٨-١٥.

«أوّلًا نجمّد baseline للكود الحالي... أي اختلاف يظهر بعد ذلك يجب أن يُنسب إلى
التعديل الأخير فقط.»

يُشغَّل قبل الحزمة وبعد كل خطوة منها: يطبع الحقائق نفسها بالترتيب نفسه، فأي
انحراف يظهر فورًا وينسب إلى آخر تعديل. لا يكتب شيئًا ولا يلمس النواة.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
# م-58 (2026-08-28): كان مثبتًا على venv/Scripts/python.exe (ويندوز حصرًا) فانهار
# بـFileNotFoundError على أي بيئة أخرى — صار sys.executable مع تفضيل venv إن وُجد.
_candidate = ROOT / "venv" / "Scripts" / "python.exe"
PY = str(_candidate) if _candidate.exists() else sys.executable
sys.stdout.reconfigure(encoding="utf-8")

# 552 انتقلت 2.8.1 → 2.8.2 بإغلاق البوّابة بأمره (ع٢)، لا بتعديل كود.
FROZEN = {"578_منفذ_التحوط": "2.9.0", "552_مدقق_الأمر": "2.8.2"}
GUARDS = ("hedge_contract", "hedge_chain", "weight_contract", "405_contract",
          "409_contract", "conviction_contract", "166_contract", "budget_contract",
          "stop_contract", "dispatch_contract", "specs_contract", "shutdown_contract",
          "protection_state_contract", "held_direction_contract", "limits_state_contract",
          "hot_reload_state_contract", "delta_visibility_contract",
          "request_id_identity_contract", "reference_alignment_contract",
          "switch_safety_contract", "stop_path_contract")


def effective(path: Path) -> int:
    return len([l for l in path.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")])


def card(folder: str) -> str:
    return (ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8")


def run(args, timeout=1800):
    return subprocess.run([PY, *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(ROOT), timeout=timeout)


def main() -> int:
    bad = 0
    print("=" * 92)
    print("الحالة المرجعيّة — %s" % ROOT)
    print("=" * 92)

    print("\n  ١· النسخ المجمَّدة (كود = بطاقة):")
    for folder, want in FROZEN.items():
        text = card(folder)
        got_card = (re.search(r'^version:\s*"([^"]+)"', text, re.M) or [None, "?"])[1]
        code = (ATOMS / folder / "atom.py").read_text(encoding="utf-8")
        got_code = (re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', code, re.M) or [None, "?"])[1]
        ok = got_card == want == got_code
        bad += 0 if ok else 1
        print("      %-22s بطاقة=%-8s كود=%-8s %s"
              % (folder.split("_")[0], got_card, got_code, "✓" if ok else "✗ المتوقّع %s" % want))

    print("\n  ٢· حدّ الأسطر (لا عودة إلى ٣٠٩):")
    n = effective(ATOMS / "578_منفذ_التحوط" / "atom.py")
    ok = n <= 300
    bad += 0 if ok else 1
    print("      578 = %d سطرًا فعليًّا %s" % (n, "✓" if ok else "✗ فوق الحدّ"))

    print("\n  ٣· حالة البوّابة — تُسجَّل ولا تُغيَّر لأجل الحارس:")
    for folder in ("552_مدقق_الأمر", "575_مرسل_الإدارة"):
        value = (re.search(r'^\s{2}enabled:\s*(\S+)', card(folder), re.M) or [None, "?"])[1]
        print("      %-22s enabled = %s" % (folder.split("_")[0], value))

    print("\n  ٤· الترميز — لا BOM:")
    bom = 0
    for path in list((ROOT / "سياق").glob("*.md")) + list((ROOT / "governance" / "checks").glob("*.py")):
        raw = path.read_bytes()
        if raw[:3] == b"\xef\xbb\xbf":
            bom += 1
            print("      ✗ %s" % path.name)
    bad += bom
    print("      ملفّات ملوّثة: %d %s" % (bom, "✓" if not bom else "✗"))

    print("\n  ٥· المدقّق · الاختبارات · الختم · تطابق النسخ:")
    out = run(["governance/scripts/validate_atoms.py"]).stdout
    # آخر سطر يذكر «مخالفات» هو «لا مخالفات.» بلا رقم — نأخذ السطر ذا العدد.
    found = re.findall(r"مخالفات\s+(\d+)", out)
    violations = int(found[-1]) if found else -1
    bad += 0 if violations == 0 else 1
    print("      المدقّق          مخالفات=%d %s" % (violations, "✓" if violations == 0 else "✗"))

    out = run(["governance/scripts/test_atoms.py"]).stdout
    atoms_ok = "212/212" in out
    passing = re.search(r"اختبارات ناجحة:\s*(\d+)", out)
    count = int(passing.group(1)) if passing else -1
    ok = atoms_ok and count >= 898
    bad += 0 if ok else 1
    print("      اختبارات الذرّات 212/212=%s · ناجحة=%d %s" % (atoms_ok, count, "✓" if ok else "✗"))

    result = run(["governance/scripts/freeze_core.py", "verify"])
    ok = result.returncode == 0
    bad += 0 if ok else 1
    print("      الختم            %s" % ("✓ سليم" if ok else "✗ منتهك"))

    result = run(["governance/checks/check_versions.py"])
    ok = "mismatches=0" in (result.stdout or "")
    bad += 0 if ok else 1
    print("      تطابق النسخ      %s" % ("✓ صفر اختلاف" if ok else "✗"))

    print("\n  ٦· الحرّاس:")
    red = []
    for name in GUARDS:
        code = run(["governance/checks/check_%s.py" % name]).returncode
        if code != 0:
            red.append(name)
    bad += len(red)
    print("      %d/%d خضراء%s" % (len(GUARDS) - len(red), len(GUARDS),
                                   "" if not red else " · الحمراء: " + " · ".join(red)))

    print("\n" + "=" * 92)
    print("انحرافات عن المرجع = %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
