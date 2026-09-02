#!/usr/bin/env python3
"""Read-only execution safety inspection through Build Registry.

The checker may use atom IDs as identity, but never assumes a filesystem path.
It is a policy checker outside Core; it does not start or mutate execution.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))
from build_registry import BuildRegistry
MQL5 = ROOT / "mt5" / "QUANT_NQ.mq5"
ACTIVE_EX5 = ROOT / "mt5" / "QUANT_NQ.ex5"
CTRADER = ROOT / "ctrader" / "QuantNQ_Feed.cs"


def _version(value: Any) -> tuple[int, int, int]:
    m = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def _record(snapshot, atom_id: int):  # noqa: ANN001
    matches = snapshot.find_atom(atom_id, scope="forex")
    return matches[0] if len(matches) == 1 else None


def _manifest(record) -> dict[str, Any]:  # noqa: ANN001
    if record is None or not record.manifest_path:
        return {}
    try:
        return yaml.safe_load(Path(record.manifest_path).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _source(record, *names: str) -> str:  # noqa: ANN001
    if record is None:
        return ""
    base = Path(record.path)
    parts: list[str] = []
    for name in names:
        path = base / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def inspect(project_root: Path | None = None) -> tuple[bool, list[str]]:
    root = (project_root or ROOT).resolve()
    snapshot = BuildRegistry(root).refresh()
    problems: list[str] = []
    records = {atom_id: _record(snapshot, atom_id) for atom_id in (516, 552, 578, 584, 708)}

    for atom_id, record in records.items():
        if record is None:
            problems.append(f"Registry لا يجد ذرة الفوركس ذات الهوية {atom_id}")

    record_578 = records[578]
    manifest_578 = _manifest(record_578)
    source_578 = _source(record_578, "atom.py", "flood_guard.py")
    actual_version = _version(manifest_578.get("version"))
    if record_578 is not None and actual_version < (2, 1, 0):
        problems.append(f"578 إصدارها {manifest_578.get('version')}; المطلوب ≥ 2.1.0 مع حارس الفيضان")
    config_578 = manifest_578.get("config") if isinstance(manifest_578.get("config"), dict) else {}
    schema_578 = manifest_578.get("config_schema") if isinstance(manifest_578.get("config_schema"), dict) else {}
    props_578 = schema_578.get("properties") if isinstance(schema_578.get("properties"), dict) else {}
    if record_578 is not None and ("resend_hold_s" not in props_578 or "resend_hold_s" not in config_578):
        problems.append("578 لا تعلن resend_hold_s في العقد والإعداد")
    if record_578 is not None and "resend_hold_s" not in source_578:
        problems.append("578 لا تحتوي حارس resend_hold_s في الكود")
    if record_578 is not None and not all(word in source_578.lower() for word in ("broker", "snapshot")):
        problems.append("578 لا تثبت مقارنة صورة الوسيط الفعلية قبل إعادة الإرسال")

    record_552 = records[552]
    manifest_552 = _manifest(record_552)
    if record_552 is not None and bool((manifest_552.get("config") or {}).get("enabled", False)):
        source_552 = _source(record_552, "atom.py", "order_validation.py")
        missing: list[str] = []
        if "_perpetual_budget_contract" not in source_552:
            missing.append("552 بلا عقد الرجل الدائمة")
        if "PROTECTION_PERPETUAL" not in source_578:
            missing.append("578 ما زالت تلصق ستوب وسيط على الرجل الدائمة")
        if "_protection_blocks" not in source_578:
            missing.append("578 بلا أمر تحوط ذري — يُنفَّذ نصفه فيصفّي المركز")
        if actual_version < (2, 5, 0):
            missing.append(f"578 إصدارها {manifest_578.get('version')}; فتح البوابة يتطلب ≥ 2.5.0")
        if missing:
            problems.append("552 enabled=true بلا إصلاحاتها: " + " · ".join(missing))

    manifest_584 = _manifest(records[584])
    if records[584] is not None and float((manifest_584.get("config") or {}).get("stop_buffer", 0.0)) <= 0.0:
        problems.append("584 stop_buffer=0 — لا وسادة أمان فوق حد الوسيط")

    manifest_708 = _manifest(records[708])
    if records[708] is not None and not ((manifest_708.get("config") or {}).get("broker_map") or {}):
        problems.append("708 broker_map فارغ — لا يجوز تفعيل أصل قبل مطابقة رمز الوسيط صراحة")

    if MQL5.is_file():
        bridge = MQL5.read_text(encoding="utf-8", errors="replace")
        if "EnsureSymbol" not in bridge or "SYMBOL_UNAVAILABLE_AT_BROKER" not in bridge:
            problems.append("إكسبرت MT5 بلا مسار ضمّ رمز صريح مع إبلاغ رفض الوسيط (EnsureSymbol)")
        if "InpMaxCmdAgeSec  = 2" not in bridge:
            problems.append("إكسبرت MT5 لا يفرض عمر أمر أقصاه ثانيتان")
        command_block = bridge.split("CREATE TABLE IF NOT EXISTS commands", 1)[-1].split(");", 1)[0]
        if "account_id TEXT" not in command_block:
            problems.append("جسر MT5 لا يضع account_id داخل جدول commands — خطر تنفيذ مزدوج بين الطرفيات")
        if "CHECK (id=1)" in bridge and "CREATE TABLE IF NOT EXISTS account" in bridge:
            problems.append("جسر MT5 يفرض صف حساب واحد — تعدد الحسابات غير آمن")
    if ACTIVE_EX5.is_file():
        problems.append("QUANT_NQ.ex5 موجود في مسار التشغيل دون إثبات بصمته مع المصدر — انقله للأرشيف أو أعد Compile")

    manifest_516 = _manifest(records[516])
    cfg_516 = manifest_516.get("config") if isinstance(manifest_516.get("config"), dict) else {}
    if records[516] is not None and (
        float(cfg_516.get("max_daily_loss_pct", 0)) >= 999
        or int(cfg_516.get("max_consecutive_losses", 0)) >= 999
        or int(cfg_516.get("max_daily_trades", 0)) >= 100000
        or int(cfg_516.get("max_open_trades", 0)) >= 100
    ):
        problems.append("516 حدود قاطع الأمان موضوعة كقيم تعطيل (999/100000) — القاطع لا يملك أسنانًا")

    if CTRADER.is_file():
        cbot = CTRADER.read_text(encoding="utf-8", errors="replace")
        if '[Parameter("Symbols (comma separated)"' not in cbot:
            problems.append("cTrader Feed بلا قائمة رموز Parameter بيد المالك")
    return not problems, problems


def main() -> int:
    ok, problems = inspect()
    print("فحص سلامة التنفيذ عبر Build Registry")
    print("EXECUTION_SAFETY=" + ("READY" if ok else "BLOCKED"))
    if ok:
        print("✅ أهداف التنفيذ اكتُشفت بالهوية لا بالمسار؛ يمكن متابعة فحوص البيئة فقط.")
        return 0
    for problem in problems:
        print("❌ " + problem)
    print("لا يجوز تشغيل التنفيذ أو اعتبار الحزمة جاهزة قبل إدخال النسخة الصحيحة.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
