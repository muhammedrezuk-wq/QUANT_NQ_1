#!/usr/bin/env python3
"""حارس تجميد المشروع — الورقة 2026-08-27 (تجميد ما بعد Release Candidate).

العقد:
    Freeze Manifest (governance/freeze/project_freeze.json)
        ↓
    SHA-256 baseline (لكل ملف مجمد بصمة من نسخة الـRC المرجعية)
        ↓
    Freeze Gate (هذا الملف)
        ↓
    رفض أي تغيير: PROJECT_FROZEN بخروج غير صفري

ما يُفحص:
    1) freeze_status == ACTIVE (وإلا فشل مغلق: FREEZE_MANIFEST_INVALID)
    2) كل ملف في frozen_files: موجود وبصمته مطابقة (modified/missing = انتهاك)
    3) لا ملف محتوى جديد داخل الجذور المجمدة (added = انتهاك) — التجميد
       يمنع الإضافة كما يمنع التعديل، وإلا صار كل ملف جديد ثغرة.

مستبعدات معلَنات لا استثناءات خفية:
    * المانيفست نفسه: مرجع الثقة — لا يحوي بصمة نفسه (استحالة ذاتية)؛
      تعديله هو ذاته قرار فتح التجميد ويوثَّق في سياسة المانيفست.
    * مخلفات لا تنتمي للإصدار: .git / __pycache__ / .pytest_cache /
      node_modules / var / *.pyc / *.pyo (نفس قواعد بناء حزمة الـRC).

لا auto_unfreeze ولا force_reload ولا bypass_freeze في هذا الملف إطلاقًا.
فتح التجميد: قرار مالك صريح → نافذة تغيير → اختبار → RC جديد → تجميد جديد.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "governance" / "freeze" / "project_freeze.json"

_CHUNK_BYTES = 65536
_EXCLUDED_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", "node_modules", "var"}
_EXCLUDED_SUFFIXES = (".pyc", ".pyo")


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_tree_files() -> list[str]:
    collected: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIR_NAMES)
        for name in sorted(filenames):
            if name.endswith(_EXCLUDED_SUFFIXES):
                continue
            collected.append(
                (Path(dirpath) / name).relative_to(ROOT).as_posix())
    return collected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="حارس التجميد — يرفض أي تغيير على الملفات المجمدة")
    parser.add_argument("--quiet", action="store_true",
                        help="لا يطبع التفاصيل، رمز الخروج فقط")
    args = parser.parse_args()

    def out(message: str) -> None:
        if not args.quiet:
            print(message)

    # ── 1) قراءة المانيفست — فشل مغلق إن غاب أو لم يكن ACTIVE
    if not MANIFEST_PATH.is_file():
        out("FREEZE_MANIFEST_INVALID: governance/freeze/project_freeze.json مفقود")
        return 2
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out(f"FREEZE_MANIFEST_INVALID: {type(exc).__name__}")
        return 2
    if manifest.get("freeze_status") != "ACTIVE":
        out(f"FREEZE_MANIFEST_INVALID: freeze_status={manifest.get('freeze_status')!r}")
        return 2
    frozen: dict[str, str] = {
        str(k): str(v) for k, v in manifest.get("frozen_files", {}).items()}
    if not frozen:
        out("FREEZE_MANIFEST_INVALID: frozen_files فارغة")
        return 2
    manifest_rel = manifest.get(
        "manifest_path", "governance/freeze/project_freeze.json")
    # ملفات مفوَّضة معلَنة: بيانات مشتقة يحرسها نظامها المالك (حارس 007) —
    # لا تُقارن ببصمة هنا (استحالة نقطة ثابتة: كلٌّ يحمل بصمة الآخر)، وهي
    # معرَّفة في delegated_files بالمانيفست لا في كود صامت.
    delegated = {str(x) for x in manifest.get("delegated_files", {})}

    # ── 2) بصمات الملفات المجمدة (modified / missing)
    violations: list[tuple[str, str]] = []
    for rel in sorted(frozen):
        path = ROOT / rel
        if not path.is_file():
            violations.append(("missing", rel))
            continue
        if _sha256_of_file(path) != frozen[rel]:
            violations.append(("modified", rel))

    # ── 3) لا إضافات داخل النطاق المجمد (added) — المفوَّضة مستثناة معلَنة
    known = set(frozen) | {manifest_rel} | delegated
    for rel in _iter_tree_files():
        if rel not in known:
            violations.append(("added", rel))

    # ── 4) الحكم
    if violations:
        out("PROJECT_FROZEN — التجميد نشط، رُفضت الحالة الحالية:")
        for kind, rel in violations:
            out(f"  [{kind}] {rel}")
        out(f"CHANGED_FILES = {len(violations)}")
        return 1

    out("PROJECT_FREEZE = PASS")
    out(f"CHANGED_FILES = 0")
    out(f"FROZEN_FILES = {len(frozen)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
