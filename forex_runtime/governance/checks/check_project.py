#!/usr/bin/env python3
"""البوابة الموحدة لفحص المشروع قبل تسليمه أو تشغيله."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CHECKS = (
    ROOT / "governance" / "checks" / "check_governance.py",
    ROOT / "governance" / "checks" / "check_files.py",
    ROOT / "governance" / "checks" / "check_events.py",
    ROOT / "governance" / "scripts" / "validate_atoms.py",
    ROOT / "governance" / "scripts" / "check_security.py",
    ROOT / "governance" / "checks" / "check_execution_safety.py",
    ROOT / "governance" / "checks" / "check_boot.py",
)


def main() -> int:
    print("فحص مشروع QUANT_NQ — بنيوي وعقود أحداث، بلا تشغيل أوامر")
    failures = 0
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    for check in CHECKS:
        print("\n" + "=" * 72)
        print(f"تشغيل: {check.relative_to(ROOT)}")
        result = subprocess.run(
            [sys.executable, str(check)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (result.stdout or "") + (result.stderr or "")
        print(output.rstrip())
        if result.returncode != 0:
            failures += 1

    print("\n" + "=" * 72)
    if failures:
        print(f"❌ فحص المشروع توقف: {failures} فحص فشل")
        return 1
    print("✅ فحص المشروع البنيوي ناجح.")
    print("هذا يثبت الملفات والمانيفستات وعقود الأحداث فقط؛ الإثبات الحي يحتاج MT5 وcTrader فعليين.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
