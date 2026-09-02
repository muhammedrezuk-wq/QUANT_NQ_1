# -*- coding: utf-8 -*-
"""تطبيق جدولي أوزان المحلّلين (المرحلة C — ورقة Scalping Micro-Tick v3.0) على قاعدة الإعدادات الحيّة.

التشغيل (من داخل مجلد المشروع — والنظام موقوف):
    py tools\set_scalp_weights.py
    (اختياري) py tools\set_scalp_weights.py --account 10096831 --symbol EURUSD

ماذا يفعل:
  1. يحدد قاعدة الإعدادات الحيّة:
       - مسار متغيّر البيئة QUANT_ANALYSIS_SETTINGS_DB إن وُجد (كما يضبطه
         run_forex.py عند الإقلاع: var/forex/analysis_settings.db)
       - وإلا var/forex/analysis_settings.db (مسار فوركس الحيّ)
       - وإلا var/store/analysis_settings.db (الافتراضي العام)
  2. يكتشف النطاقات الحقيقية (حساب+وسيط+رمز) من:
       - صفوف موجودة في قاعدة الإعدادات
       - لقطات الذرّات الحيّة var/forex/snapshots/*.json (cycle_id/account_id/symbol)
       - معاملين يدويين --account و --symbol
  3. يكتب جدولَي أوزان الورقة (سريع §10 · بطيء §11-12) لكل نطاق مكتشف
     دفعةً واحدة — بلا إعادة توزيع وبلا مساس بالأعماق والعتبات.
  4. يقرأ للتحقق (المجموع 100 في كل جدول) ويطبع النتيجة.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _find_project_root(start: Path) -> Path:
    """يصعد من موقع السكربت حتى يجد جذر المشروع (حيث توجد shared/)."""
    candidates = [start, *start.parents]
    cwd = Path.cwd()
    if cwd not in candidates:
        candidates.append(cwd)
    for candidate in candidates:
        if (candidate / "shared" / "live_analysis.py").is_file():
            return candidate
    return start


ROOT = _find_project_root(_HERE)
sys.path.insert(0, str(ROOT))

# ── جدولا الورقة — المرحلة C (config فقط، بختم nq) ────────────────────────
FAST_WEIGHTS = {
    "velocity": 25.0, "momentum": 20.0, "acceleration": 20.0, "spread": 12.0,
    "volatility": 8.0, "noise": 10.0, "volume_quality": 5.0, "volume": 0.0,
    "trend": 0.0, "candle": 0.0, "gap": 0.0, "session": 0.0, "time": 0.0,
    "correlation": 0.0, "relative_strength": 0.0,
}
SLOW_WEIGHTS = {
    "trend": 25.0, "momentum": 15.0, "candle": 15.0, "relative_strength": 10.0,
    "volume_quality": 10.0, "session": 10.0, "gap": 5.0, "correlation": 5.0,
    "time": 5.0, "velocity": 0.0, "acceleration": 0.0, "spread": 0.0,
    "volatility": 0.0, "noise": 0.0, "volume": 0.0,
}
ALL_ANALYZERS = list(FAST_WEIGHTS.keys())

_CYCLE_RE = re.compile(r'"cycle_id"\s*:\s*"([^"]+)"')


def _pick_db_path(configured: str | None) -> Path:
    """مسار قاعدة الإعدادات: متغيّر البيئة إن وُجد (كما يضبطه run_forex.py)،
    وإلا مسار فوركس الحيّ var/forex/analysis_settings.db دومًا —
    لأنه المسار الذي يقرؤه النظام الحيّ (run_forex.py) واللوحة (governance)."""
    forex_live = ROOT / "var" / "forex" / "analysis_settings.db"
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else ROOT / p
    return forex_live


def _discover_scopes_from_snapshots() -> list[tuple[str, str, str]]:
    """يستخرج (حساب, وسيط, رمز) من لقطات الذرّات الحيّة.

    البنية الحقيقية (live_analysis.snapshot): مفاتيح `states` هي نطاقات
    ثلاثية على هيئة سلسلة JSON: '["الحساب", "الوسيط", "الرمز"]'.
    نمرّ على بنية JSON كاملة ونفكّ المفاتيح الشبيهة بمصفوفة ثلاثية.
    """
    found: dict[tuple[str, str, str], None] = {}
    snap_dir = ROOT / "var" / "forex" / "snapshots"
    if not snap_dir.is_dir():
        return []
    for f in sorted(snap_dir.glob("*.json")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, (dict, list)):
            stack = [data]
            while stack:
                obj = stack.pop()
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(k, str):
                            try:
                                parts = json.loads(k)
                            except (json.JSONDecodeError, TypeError):
                                parts = None
                            if (isinstance(parts, list) and len(parts) == 3
                                    and all(isinstance(p, str) and p.strip() for p in parts)):
                                found[(parts[0].strip(), parts[1].strip(), parts[2].strip())] = None
                        stack.append(v)
                elif isinstance(obj, list):
                    stack.extend(obj)
        # fallback: cycle_id بصيغة a|b|s|... إن كانت اللقطة غير JSON كاملة
        for m in _CYCLE_RE.finditer(text):
            parts = [p.strip() for p in m.group(1).split("|")]
            if len(parts) >= 3 and parts[0] and parts[1] and parts[2]:
                found[(parts[0], parts[1], parts[2])] = None
    return list(found.keys())


def main() -> int:
    parser = argparse.ArgumentParser(description="تطبيق أوزان المرحلة C على القاعدة الحيّة")
    parser.add_argument("--account", help="رقم الحساب الحقيقي (اختياري — يُكتشف تلقائيًا)")
    parser.add_argument("--symbol", help="الرمز الحقيقي مثل EURUSD (اختياري — يُكتشف تلقائيًا)")
    args = parser.parse_args()

    configured = os.environ.get("QUANT_ANALYSIS_SETTINGS_DB")
    db_path = _pick_db_path(configured)
    print(f"[1/5] قاعدة الإعدادات: {db_path}")

    from shared.live_analysis import AnalysisSettingsStore

    try:
        store = AnalysisSettingsStore(str(db_path))
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ تعذّر فتح قاعدة الإعدادات: {exc}")
        return 2

    # 1) نطاقات من صفوف القاعدة الموجودة
    scopes: dict[tuple[str, str, str], None] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT account_id, broker, symbol FROM analysis_settings"
            ).fetchall()
        for r in rows:
            scopes[(str(r[0]), str(r[1]), str(r[2]))] = None
    except sqlite3.Error as exc:
        print(f"⚠ تعذّر قراءة القاعدة بعد البناء: {exc}")
        return 2

    # 2) نطاقات من اللقطات الحيّة
    discovered = _discover_scopes_from_snapshots()
    for s in discovered:
        scopes[s] = None
    if discovered:
        print(f"[2/5] اكتشفت {len(discovered)} نطاقًا من اللقطات الحيّة: "
              + " · ".join(f"{a}/{b}/{s}" for a, b, s in discovered[:5]))

    # 3) نطاق يدوي
    if args.account and args.symbol:
        scopes[(args.account.strip(), "Raw Trading Ltd", args.symbol.strip().upper())] = None

    if not scopes:
        scopes[("A", "Raw Trading Ltd", "NQ")] = None
        print("[2/5] لا نطاقات مكتشفة ولا لقطات — أكتب للنطاق المرجعي "
              "(A · Raw Trading Ltd · NQ).")
        print("      ⚠ ليطبّق على سوقك الفعلي: شغّل النظام أولًا دقيقة ثم أوقفه، "
              "وأعد هذا السكربت — سيلتقط نطاقك من اللقطات.")
        print("      أو مرّره يدويًا: py tools\\set_scalp_weights.py "
              "--account <رقم حسابك> --symbol <الرمز>")
    else:
        print(f"[2/5] النطاقات المستهدفة ({len(scopes)}): "
              + " · ".join(f"{a}/{b}/{s}" for a, b, s in list(scopes)[:8]))

    command_id = f"SCALP-PHASEC-{time.strftime('%Y%m%d-%H%M%S')}"
    changed_at = time.time()

    written = 0
    for account, broker, symbol in scopes:
        for path, table in (("fast", FAST_WEIGHTS), ("slow", SLOW_WEIGHTS)):
            store.set_weights(account, broker, symbol, table,
                              changed_by="NQ", command_id=command_id,
                              changed_at=changed_at, path=path)
            written += 1
    print(f"[3/5] ✅ كُتب {written} جدولًا (سريع+بطيء) لـ {len(scopes)} نطاقًا "
          f"بأمر {command_id}")

    print("[4/5] التحقق بالقراءة:")
    ok = True
    for account, broker, symbol in scopes:
        for path in ("fast", "slow"):
            vals = {a: store.get(account, broker, symbol, a, path)["weight"]
                    for a in ALL_ANALYZERS}
            total = round(sum(vals.values()), 2)
            nonzero = {a: v for a, v in vals.items() if v}
            marker = "✅" if total == 100.0 else "⚠ المجموع ليس 100!"
            if total != 100.0:
                ok = False
            print(f"  {marker} {account} · {broker} · {symbol} · {path}: "
                  f"المجموع={total} → {nonzero}")

    others = [p for p in (ROOT / "var").rglob("analysis_settings*.db")
              if p.is_file() and p.resolve() != db_path.resolve()]
    if others:
        print("\n⚠ انتبه — قواعد إعدادات أخرى في المشروع (قد يقرأ النظام منها):")
        for p in others:
            print(f"   • {p}")
    else:
        print("\nلا توجد قواعد أخرى — المسار المعتمد وحيد. ✅")

    if not ok:
        print("\n⚠ فشل التحقق — راجع الأرقام أعلاه.")
        return 1
    print("\n[5/5] تم بنجاح. أعد تشغيل النظام ثم افتح اللوحة → إعدادات التحليل "
          "وتأكد من القيم الجديدة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())