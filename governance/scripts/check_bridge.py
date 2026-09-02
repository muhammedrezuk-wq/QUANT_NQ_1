#!/usr/bin/env python3
"""قراءة فقط: فحص جسر MT5 قبل التشغيل.
لا ينشئ أمرًا ولا يفتح صفقة ولا يغيّر أي جدول.
"""
from __future__ import annotations

import os
import sqlite3

DEFAULT_DB = os.path.join(os.environ.get("APPDATA", r"C:\Users\NQ\AppData\Roaming"), "MetaQuotes", "Terminal", "Common", "Files", "nq_brain.db")
BLOCKED_ACCOUNT_IDS = set()
TABLES = ("account", "ticks", "symbol_specs", "candles_history", "positions", "trade_events", "commands")


def main() -> int:
    path = os.environ.get("NQ_BRIDGE_DB", "").strip() or DEFAULT_DB
    print("فحص جسر MT5 — قراءة فقط")
    print(f"الملف: {path}")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        print(f"❌ لا أستطيع قراءة ملف الجسر: {exc}")
        return 2
    try:
        names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        print("الجداول:")
        for table in TABLES:
            if table not in names:
                print(f"  ❌ {table}")
                continue
            try: count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error: count = "؟"
            print(f"  ✅ {table}: {count}")
        if "account" not in names: return 2
        row = con.execute("SELECT account_id, broker, account_server, connected, trade_allowed, expert_allowed FROM account WHERE id=1").fetchone()
        if not row or not row[0]:
            print("❌ هوية الحساب غير موجودة — النظام سيبقى مقفولًا.")
            return 3
        account_id = str(row[0])
        print(f"الحساب المتصل: {account_id}")
        print(f"الوسيط/الخادم: {row[1] or '—'} / {row[2] or '—'}")
        print(f"متصل/تداول/إكسبرت: {bool(row[3])} / {bool(row[4])} / {bool(row[5])}")
        if account_id in BLOCKED_ACCOUNT_IDS:
            print("🛑 هذا الحساب موجود في قائمة المنع — توقف.")
            return 4
        if not bool(row[3]) or not bool(row[4]) or not bool(row[5]):
            print("⚠ الاتصال أو التداول الآلي غير مفعّل.")
            return 5
        print("✅ الحساب الحالي مقروء والجسر متصل. 601 سيستخدمه تلقائيًا، ويتوقف إذا تغيّر.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
