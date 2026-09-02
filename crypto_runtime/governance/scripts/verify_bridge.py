#!/usr/bin/env python3
"""
scripts/verify_bridge.py — يتحقق أن طرفَي جسر nq_brain.db على مسار واحد
============================================================================
لماذا هذا السكربت موجود:

فحص قاعدة البيانات الحقيقية أظهر 162,633 صفًا في `trade_signals` كتبتها بايثون
على مدى 48.7 ساعة، وفي الملف نفسه **لا وجود** لجدولَي `broker_feed` ولا
`trade_events` — وكلاهما ينشئه الـEA بـ`CREATE TABLE IF NOT EXISTS` عند أول
كتابة ناجحة. أي أن الـEA لم يلمس هذا الملف ولا مرة واحدة، والجسر كان أحادي
الاتجاه منذ اليوم الأول.

مسار مختلف بين الطرفين لا يُسقط أي اختبار ولا يرفع أي استثناء: كل طرف ينجح
تمامًا على ملفه هو. لذلك لا يكشفه إلا فحص صريح — وهذا هو.

الاستعمال:
    python3 scripts/verify_bridge.py              # يتحقق فقط
    python3 scripts/verify_bridge.py --show       # يطبع الحالة بالتفصيل

يُرجع 0 عند الاتفاق، و1 عند الاختلاف.

ملاحظة معمارية: لا يوجد ملف إعداد مركزي في هذا المشروع عمدًا — الإعداد يعيش
في مانيفست كل ذرة، واعتماد النواة على اسم ملف خارجي مخالفة معمارية. فالمكافئ
الصحيح لـ"مصدر واحد للحقيقة" هنا هو هذا الفحص: القيمة مكرَّرة، والتكرار
مضبوط آليًا.
"""

from __future__ import annotations

import argparse
import ntpath
import os
import posixpath
import sqlite3
import sys
from pathlib import Path

import yaml

# الطرفية على ويندوز قد تكون على صفحة ترميز قديمة (cp437/cp1256)، فتُعرض
# مخرجات UTF-8 حروفًا مشوّشة. نفرض UTF-8 على مخرجنا، ونستبدل ما يعجز عنه
# المحرف بدل أن نرمي UnicodeEncodeError ونُسقط السكربت.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(PROJECT_ROOT)

ATOMS_ROOT = ATOM_ROOT

# طرفا الجسر: من يكتب الإشارة، ومن يقرأ نتيجة التنفيذ.
BRIDGE_ATOMS = {
    601: "writes trade_signals  (Python -> EA)",
    563: "reads  trade_events   (EA -> Python)",
}

# ما يجب أن يوجد في قاعدة عاملة، ومن ينشئه.
EXPECTED_TABLES = {
    "trade_signals": "601 (Python)",
    "trade_events": "the EA",
    "broker_feed": "the EA",
}


def _load_manifests() -> dict[int, dict]:
    found: dict[int, dict] = {}
    for manifest_path in ATOMS_ROOT.rglob("manifest.yaml"):
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if data["id"] in BRIDGE_ATOMS:
            data["_path"] = manifest_path
            found[data["id"]] = data
    return found


def _inspect(db_path: Path) -> dict:
    """يفحص القاعدة دون لمسها: يفتح للقراءة فقط."""
    if not db_path.exists():
        return {"exists": False}
    report: dict = {"exists": True, "size": db_path.stat().st_size, "tables": {}}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        report["error"] = str(exc)
        return report
    try:
        report["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for name in names:
            report["tables"][name] = conn.execute(
                f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    finally:
        conn.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify both sides of the nq_brain.db bridge agree on one path")
    parser.add_argument("--show", action="store_true", help="print database state in detail")
    args = parser.parse_args()

    manifests = _load_manifests()
    missing = sorted(set(BRIDGE_ATOMS) - set(manifests))
    if missing:
        print(f"[FAIL] bridge atoms not found: {missing}")
        return 1

    print("Database path on each side:")
    paths: dict[int, str] = {}
    for atom_id, role in BRIDGE_ATOMS.items():
        # م-58 (ورقة ٤١، 2026-08-28): مانيفست بلا db_path كان ينهار بـKeyError —
        # يُتخطّى بتشخيص صريح (ذرّات المحاكاة والجسور الجديدة).
        value = manifests[atom_id]["config"].get("db_path")
        if value is None:
            print(f"تخطّي {atom_id}: لا db_path بالمانيفست")
            continue
        paths[atom_id] = value
        print(f"  {atom_id}  {role}")
        print(f"       db_path = {value!r}")

    distinct = set(paths.values())
    if len(distinct) != 1:
        print()
        print("[FAIL] The two sides point at different files. Each will succeed on its")
        print("       own file and never see the other - exactly what the inspected")
        print("       database showed.")
        return 1

    shared = next(iter(distinct))
    print(f"\n[OK] Both sides agree on: {shared}")

    # يُفحَص بقواعد النظامين معًا: هذا السكربت قد يُشغَّل على غير النظام الذي
    # يحمل القاعدة، ومسار ويندوز مثل C:\... تراه pathlib على لينكس نسبيًّا.
    if not (ntpath.isabs(shared) or posixpath.isabs(shared)):
        print()
        print("[WARN] Relative path. Python resolves it from the working directory,")
        print("       the EA resolves it inside the MT5 sandbox - two different files")
        print("       under one name. Run BridgeProbe.mq5 and use the path that worked.")
        return 1

    if not args.show:
        return 0

    print("\nDatabase state:")
    if ntpath.isabs(shared) and not posixpath.isabs(shared) and os.name != "nt":
        print("  Windows path, and this check is running elsewhere - cannot open it.")
        print("  Run this on the machine holding the database to see the tables.")
        return 0
    report = _inspect(Path(shared))
    if not report["exists"]:
        print("  No file at this path yet.")
        return 0
    print(f"  size        : {report['size']:,} bytes")
    print(f"  journal_mode: {report.get('journal_mode')}"
          + ("" if report.get("journal_mode") == "wal"
             else "   [WARN] not WAL - a writer blocks every reader"))
    print("  tables:")
    for table, owner in EXPECTED_TABLES.items():
        if table in report["tables"]:
            print(f"    [OK]   {table:<15} {report['tables'][table]:>9,} rows   (written by {owner})")
        else:
            print(f"    [MISS] {table:<15} {'absent':>9}   (written by {owner} - never has)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
