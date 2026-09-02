#!/usr/bin/env python3
"""قراءة فقط: يحدد لماذا بوابة التنفيذ مفتوحة أو مقفولة أو موقوفة."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry import BuildRegistry

URL = "http://127.0.0.1:8010/api/atoms"


def main() -> int:
    print("تشخيص بوابة التنفيذ — قراءة فقط")
    local = BuildRegistry(ROOT).find_atom(552, scope="forex")
    if len(local) != 1:
        print("❌ Registry لا يجد هوية 552 في شجرة الفوركس.")
        return 3
    try:
        with urllib.request.urlopen(URL, timeout=5) as response:
            atoms = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"❌ لا أستطيع قراءة النواة على 8010: {exc}")
        print("النواة مطفأة أو غرفة القيادة متصلة بنسخة أخرى.")
        return 2
    gate = next((a for a in atoms if int(a.get("id", -1)) == 552), None)
    if gate is None:
        print("❌ الذرة 552 غير موجودة في النواة التي تعمل الآن.")
        return 3
    health = gate.get("health") or {}
    message = str(health.get("message") or "")
    print(f"نسخة 552 التي تعمل: {gate.get('version', '—')}")
    print(f"حالة الذرة: {gate.get('state', '—')}")
    print(f"رسالة صحّة 552: {message or '(فارغة — أوّل فحص صحّة لم يحن وقته بعد)'}")
    if not message or message == "NOT_STARTED":
        print("ℹ لسّا مبكّر — ما بيثبت لا فتح ولا قفل.")
        return 0
    if message == "NO_INPUT_YET":
        print("ℹ ٥٥٢ لسّا ما استقبلت أوّل أمر — هالوحدها ما تثبت حالة البوّابة (مفتوحة أو مقفولة، الاثنتان تبدآن هيك).")
        return 0
    # كود ٥٥٢ الحالي (v5.0.0+) لا ينشر إطلاقًا رسالة صحّة تحمل LIVE/HALTED/
    # PREVIEW — رسالته الوحيدة عند السلامة هي "finalized=N rejected=N"،
    # ولا تتضمّن enabled على الإطلاق. enabled موجود فقط داخل health.details
    # (غير مكشوف عبر /api/atoms اليوم) أو عبر حدث execution.gate.state الحيّ
    # (بثّ WebSocket فقط، لا HTTP). هالسكربت لا يقدر يحسم مفتوحة/مقفولة من
    # مصدره الحالي — لا تخترع جوابًا، وجّه لمصدر حقيقي بدل التخمين.
    print("⚠ هالتشخيص (HTTP GET وحيد) ما بيقدر يحسم مفتوحة/مقفولة من رسالة صحّة 552 الحاليّة —")
    print("   الحقل enabled موجود فقط بحدث execution.gate.state الحيّ (بثّ)، مو برسالة الصحّة.")
    print("   المصدر الصادق: بطاقة «بوّابة التنفيذ» بصفحة «الرئيسية» أو قسم «التنفيذ» بغرفة القيادة —")
    print("   بتقرأ execution.gate.state حيًّا وتقول مفتوحة/مقفولة بالضبط.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
