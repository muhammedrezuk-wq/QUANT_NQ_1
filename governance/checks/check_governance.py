#!/usr/bin/env python3
"""فحص طبقة الحوكمة فقط — قراءة وتحقيق بنيوي، بلا تشغيل أو كتابة تداول."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED = (
    "governance/app.py",
    "governance/server.py",
    "governance/ui/built/index.html",
    "governance/scripts/run_core.py",
    "governance/scripts/validate_atoms.py",
    "governance/scripts/test_atoms.py",
    "governance/scripts/check_health.py",
    "governance/scripts/check_bridge.py",
    "governance/scripts/check_ctrader.py",
    "governance/scripts/check_security.py",
    "governance/checks/check_files.py",
    "governance/checks/check_events.py",
    "governance/checks/check_boot.py",
    "governance/checks/check_execution_safety.py",
    "governance/checks/check_project.py",
    "governance/setup/requirements.txt",
    "governance/launchers/Control_Room.bat",
    "governance/launchers/Check_Project.bat",
    "governance/launchers/Check_Security.bat",
)


def main() -> int:
    problems: list[str] = []
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            problems.append(f"ملف حوكمة مفقود: {relative}")

    root_files = [p.name for p in ROOT.iterdir() if p.is_file()]

    shell_files = [
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*")
        if p.is_file() and p.suffix.lower() in {".sh", ".bash"}
    ]
    if shell_files:
        problems.append("وجدت ملفات Linux/Bash غير مسموحة: " + ", ".join(sorted(shell_files)))

    server_text = (ROOT / "governance/server.py").read_text(encoding="utf-8") if (ROOT / "governance/server.py").is_file() else ""
    app_text = (ROOT / "governance/app.py").read_text(encoding="utf-8") if (ROOT / "governance/app.py").is_file() else ""
    if "governance/scripts/" not in server_text:
        problems.append("خادم الحوكمة لا يشير إلى أدوات الحوكمة الجديدة")
    if '"governance" / "scripts" / "run_core.py"' not in app_text:
        problems.append("مشغّل الحوكمة لا يشير إلى مشغّل النواة داخل الحوكمة")

    print("فحص طبقة الحوكمة")
    print(f"الجذر: {ROOT}")
    print(f"الملفات المطلوبة: {len(REQUIRED)}")
    print(f"ملفات الجذر الرسمية: {len(root_files)}")
    print(f"ملفات Linux/Bash التنفيذية: {len(shell_files)}")
    if problems:
        for problem in problems:
            print("❌ " + problem)
        return 1
    print("✅ طبقة الحوكمة سليمة: أدوات وفحوصات اللوحة داخل governance، وملفات الجذر الرسمية مقبولة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
