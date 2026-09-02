# -*- coding: utf-8 -*-
"""
إعادة توليد خطوط أساس السلامة (integrity_baseline.json) بمنطق الذرّة 007/2007
================================================================================
المشروع: QUANT_NQ
التاريخ: 2026-08-27

لماذا هذه الأداة؟
  خط الأساس مولَّد من شجرة كاملة ثم رُفعت الشجرة بعد تعديلات أخيرة دون إعادة
  توليد — فسقط حارس السلامة بانتهاكات وهمية على نسخة سليمة:
    * الجذر: 3 مدخلات متقادمة من 1271 (run_forex.py, core_forex.yaml,
      unified_release.json) — سقوط tests/test_device_contract.py.
    * forex_runtime: 312 مدخلة خاطئة من 1265 (خط الأساس وُلّد قبل اقتطاع
      ملفات النشر: الشرح.md، pyproject.toml، requirements.txt...).
    * crypto_runtime: 230 من 580، مع min_watched_items=700 لا تُرضيه شجرة
      الـ533 ملفاً أصلاً (عولج في مانيفست 2007 بخفضه إلى 500).

ما تفعله:
  * تقرأ cfg المانيفست الخاص بكل شجرة (007 للجذر/الفوركس، 2007 للكريبتو)
    وتطبّق نفس منطق الذرّة حرفياً: نفس الملفات/الأدلة المراقبة، نفس الامتدادات
    والأسماء، نفس تجاهل الأدوار واللواحق، نفس تطبيع المفاتيح إلى POSIX.
  * تحافظ على ترتيب المفاتيح القائم قدر الإمكان (المفاتيح الباقية تحفظ
    مواضعها، الجديدة تُلحق بالترتيب) — فالباقي الأكبر يبقى بصماته كما هي
    والفرق يبقى قابلاً للمراجعة.
  * تعيد حساب scope_digest بمعادلة الذرّة نفسها (من cfg لا من الملفات)،
    فأي تغيير لاحق في نطاق المراقبة يظل مكتشفاً.

ما لا تفعله:
  * لا تلمس أي ملف خارج ملفات خط الأساس الثلاثة.
  * لا "تسكت" الحارس: أي تعديل حقيقي بعد التوليد سيظل يُكتشف كالسابق.

الاستخدام:
    python tools/baseline_regen.py            # يولّد الأشجار الثلاث
    python tools/baseline_regen.py --check    # يرفض أي stale/removed/added دون كتابة
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# (الشجرة، مسار مانيفست حارس السلامة، ملف خط الأساس) لكل نطاق
SCOPES = [
    (ROOT, ROOT / "atoms/قسم 001-050/007_سلامة_الملفات/manifest.yaml",
     ROOT / "integrity_baseline.json"),
    (ROOT / "forex_runtime",
     ROOT / "forex_runtime/atoms/قسم 001-050/007_سلامة_الملفات/manifest.yaml",
     ROOT / "forex_runtime/integrity_baseline.json"),
    (ROOT / "crypto_runtime",
     ROOT / "crypto_runtime/atoms/قسم 2001-2050/2007_سلامة_الملفات/manifest.yaml",
     ROOT / "crypto_runtime/integrity_baseline.json"),
]

_DEFAULT_IGNORED_DIRS = ("__pycache__", ".git", ".pytest_cache", ".mypy_cache")
_DEFAULT_IGNORED_SUFFIXES = (".pyc", ".pyo", ".pyd", ".tmp", ".log")
_CHUNK_BYTES = 65536
_MISSING = "\x00missing"


def _sha256_of_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_cfg(manifest_path: Path) -> dict:
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cfg = payload.get("config") or {}
    return {
        "watched_files": [str(v) for v in cfg.get("watched_files", [])],
        "watched_dirs": [str(v) for v in cfg.get("watched_dirs", [])],
        "watched_extensions": {str(v).lower()
                               for v in cfg.get("watched_extensions", [])},
        "watched_names": {str(v) for v in cfg.get("watched_names", [])},
        "min_watched_items": int(cfg.get("min_watched_items", 1)),
        "ignored_dirs": set(_DEFAULT_IGNORED_DIRS) | {
            str(v) for v in cfg.get("ignored_dir_names", [])},
        "ignored_suffixes": _DEFAULT_IGNORED_SUFFIXES + tuple(
            str(v) for v in cfg.get("ignored_suffixes", [])),
    }


def _scope_digest(cfg: dict) -> str:
    # نفس معادلة Atom._calculate_scope_digest حرفياً
    payload = {"files": cfg["watched_files"], "dirs": cfg["watched_dirs"],
               "extensions": sorted(cfg["watched_extensions"]),
               "names": sorted(cfg["watched_names"]),
               "minimum": cfg["min_watched_items"],
               "ignored_dirs": sorted(cfg["ignored_dirs"]),
               "ignored_suffixes": list(cfg["ignored_suffixes"])}
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _allowed(name: str, cfg: dict) -> bool:
    if name in cfg["watched_names"]:
        return True
    if not cfg["watched_extensions"]:
        return True
    return os.path.splitext(name)[1].lower() in cfg["watched_extensions"]


def _collect(base: Path, cfg: dict) -> dict[str, str]:
    # يعمل من داخل الشجرة (cwd=base) لتطابق مسارات المفاتيح النسبية سلوك
    # الذرّة عند التشغيل الفعلي — os.chdir مثل run_forex.py تماماً.
    # مهم: مفاتيح watched_files تُسجَّل دائماً حتى لو الملف مفقوداً (بقيمة
    # _MISSING) والأدلة المفقودة تسجَّل dirroot: — هذا سلوك Atom._collect
    # حرفياً، وإلا انفجر الحارس بانتهاكات "added" عند التشغيل.
    prev = os.getcwd()
    os.chdir(base)
    try:
        current: dict[str, str] = {}
        for path in cfg["watched_files"]:
            try:
                current["file:" + path] = _sha256_of_file(path)
            except FileNotFoundError:
                current["file:" + path] = _MISSING
        for dir_path in cfg["watched_dirs"]:
            if not os.path.isdir(dir_path):
                current["dirroot:" + dir_path] = _MISSING
                continue
            for root, dirs, files in os.walk(dir_path):
                dirs[:] = [n for n in dirs if n not in cfg["ignored_dirs"]]
                for name in files:
                    if name.endswith(cfg["ignored_suffixes"]) or not _allowed(name, cfg):
                        continue
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, dir_path).replace(os.sep, "/")
                    current["dir:" + dir_path + "::" + rel] = _sha256_of_file(full)
        return current
    finally:
        os.chdir(prev)


def _merge_preserving_order(old_items: dict[str, str],
                            new_items: dict[str, str]) -> dict[str, str]:
    """حافظ على مواضع المفاتيح القائمة، وألحق الجديدة بترتيب الجمع."""
    merged: dict[str, str] = {}
    for key in old_items:
        if key in new_items:
            merged[key] = new_items[key]
    for key, value in new_items.items():
        if key not in merged:
            merged[key] = value
    return merged


def regenerate(base: Path, manifest: Path, baseline_file: Path,
               write: bool = True) -> bool:
    cfg = _load_cfg(manifest)
    current = _collect(base, cfg)
    minimum = cfg["min_watched_items"]
    digest = _scope_digest(cfg)

    old = json.loads(baseline_file.read_text(encoding="utf-8"))
    old_items = old.get("items", {})
    stale = sum(1 for k, v in old_items.items()
                if current.get(k) not in (None, v))
    removed = sum(1 for k in old_items if k not in current)
    added = sum(1 for k in current if k not in old_items)
    scope_changed = old.get("scope_digest") != digest

    print(f"[{base.name or 'root'}] عناصر الحرس الحالية: {len(current)} "
          f"(الحد الأدنى {minimum}) — متقادم: {stale}، مزال: {removed}، "
          f"مضاف: {added}، النطاق: {'متغيّر' if scope_changed else 'مطابق'}")
    if len(current) < minimum:
        print("  ✗ عدد العناصر تحت الحد الأدنى — لا يُكتب خط الأساس")
        return False
    if not write and (stale or removed or added or scope_changed):
        print("  ✗ خط الأساس لا يطابق الشجرة — وضع --check لا يجدّد البصمات")
        return False

    merged = _merge_preserving_order(old_items, current)
    payload = {"format": "quant-nq-integrity-baseline",
               "scope_digest": digest,
               "items": merged}

    if write:
        baseline_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"  ✓ كُتب {baseline_file.relative_to(ROOT)} "
              f"({len(merged)} عنصراً، scope_digest={digest[:12]}…)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="إعادة توليد خطوط أساس السلامة بمنطق الذرّة 007/2007")
    parser.add_argument(
        "--check", action="store_true",
        help="تحقق فقط: يفشل عند stale/removed/added أو تغيّر نطاق الحراسة",
    )
    args = parser.parse_args()

    ok = True
    for base, manifest, baseline_file in SCOPES:
        if not manifest.exists():
            print(f"[{base}] ✗ لا يوجد مانيفست حارس: {manifest}")
            ok = False
            continue
        ok = regenerate(base, manifest, baseline_file, write=not args.check) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
