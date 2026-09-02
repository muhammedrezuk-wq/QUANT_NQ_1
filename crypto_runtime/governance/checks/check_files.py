#!/usr/bin/env python3
"""Read-only source-tree check backed by the central Build Registry."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_registry import BuildRegistry  # noqa: E402


def main() -> int:
    snapshot = BuildRegistry(ROOT).refresh()
    problems: list[str] = []

    required_dirs = ("atoms", "atoms_crypto", "config", "core", "ctrader", "governance", "mt5", "security", "shared")
    missing_dirs = [name for name in required_dirs if not (ROOT / name).is_dir()]
    problems.extend(f"مجلد مطلوب مفقود: {name}" for name in missing_dirs)
    problems.extend(f"فشل اكتشاف: {path} — {error}" for path, error in snapshot.integrity.discovery_failures)
    if snapshot.integrity.duplicate_ids:
        problems.append("تصادم Atom ID: " + ", ".join(str(item[1]) for item in snapshot.integrity.duplicate_ids))

    documentation_missing: list[str] = []
    for record in snapshot.manifests:
        atom_dir = Path(record.path)
        if not (atom_dir / "atom.py").is_file():
            problems.append(f"كود الذرة مفقود: {atom_dir / 'atom.py'}")
        if not (atom_dir / "الشرح.md").is_file():
            documentation_missing.append(str(atom_dir / "الشرح.md"))

    shell_files = [
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*")
        if p.is_file() and p.suffix.lower() in {".sh", ".bash"}
    ]
    if shell_files:
        problems.append("ملفات Linux/Bash موجودة: " + ", ".join(sorted(shell_files)))

    ex5_files = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.ex5")]
    print("فحص ملفات المشروع عبر Build Registry")
    print(f"Forex root manifests: {len(snapshot.forex_all)}")
    print(f"Crypto root manifests: {len(snapshot.crypto_all)}")
    print(f"إجمالي المانيفستات: {len(snapshot.manifests)}")
    print(f"معرّفات الذرات الفريدة: {len(snapshot.atom_ids)}")
    print(f"المجلدات المطلوبة المفقودة: {missing_dirs or 'لا شيء'}")
    print(f"ملفات الخبراء المترجمة: {len(ex5_files)}")
    print(f"Documentation: {'INCOMPLETE' if documentation_missing else 'COMPLETE'}")
    if documentation_missing:
        # إصلاح م-29 (ورقة ٤١، بأمر المالك 2026-08-28): التوثيق العربي الناقص
        # عاد **حاجبًا للإصدار** كما كان — لا تحذيرًا صامتًا. الفجوة نفسها
        # سُدّت (81 ملف الشرح.md وُلّدت من المانيفستات بنفس صيغة البطاقة المقيسة).
        for item in documentation_missing:
            problems.append("توثيق عربي مفقود: " + item)
    if problems:
        for problem in problems:
            print("❌ " + problem)
        return 1
    print("✅ الفحص البنيوي للملفات والمانيفستات ناجح — والتوثيق العربي حاجب إصدار (م-29).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
