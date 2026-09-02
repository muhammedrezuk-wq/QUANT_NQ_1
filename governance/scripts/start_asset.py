#!/usr/bin/env python3
"""طلب بدء أصل واحد بالتشغيل المباشر عبر بوابة 901 فقط.

هذا السكربت لا يكتب أوامر MT5 مباشرة، ولا يحسب لوتًا أو وقفًا.
القرار يمر من غرفة القيادة → 901 → 576 → 516/551/552 → 601.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_BRIDGE = os.path.join(os.environ.get("APPDATA", r"C:\Users\NQ\AppData\Roaming"), "MetaQuotes", "Terminal", "Common", "Files", "nq_brain.db")
KNOWN_REAL_ACCOUNT = ""

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS commands ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,"
    "operator TEXT NOT NULL, requested_at REAL NOT NULL,"
    "status TEXT NOT NULL DEFAULT 'PENDING', executed_at REAL, payload_json TEXT)"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="BTCUSD")
    ap.add_argument("budget", nargs="?", type=float, default=100.0)
    args = ap.parse_args()
    bridge = os.environ.get("NQ_BRIDGE_DB", "").strip() or DEFAULT_BRIDGE
    governance = Path(__file__).resolve().parents[2] / "var" / "governance" / "commands.db"

    try:
        con = sqlite3.connect(f"file:{bridge}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        print(f"❌ لا أستطيع قراءة جسر MT5: {exc}")
        return 2
    try:
        account = con.execute(
            "SELECT account_id, broker, account_server, connected, trade_allowed, expert_allowed "
            "FROM account WHERE id=1").fetchone()
        if account is None or not account["account_id"]:
            print("❌ لا توجد هوية حساب في الجسر.")
            return 3
        account_id = str(account["account_id"])
        print(f"الحساب: {account_id} · {account['broker'] or '—'} · {account['account_server'] or '—'}")
        if account_id == KNOWN_REAL_ACCOUNT:
            print("🛑 هذا الحساب الحقيقي المعروف — توقّف.")
            return 4
        if not bool(account["connected"]):
            print("⚠ MT5 غير متصل.")
            return 5
        if not bool(account["trade_allowed"]) or not bool(account["expert_allowed"]):
            print("⚠ التداول الآلي مقفول في MT5.")
            return 6
    finally:
        con.close()

    if not args.symbol or args.budget <= 0:
        print("اكتب رمزًا وميزانية موجبة.")
        return 7
    governance.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = sqlite3.connect(str(governance), timeout=5)
        con.execute("PRAGMA busy_timeout=3000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(SCHEMA)
        if "payload_json" not in {str(r[1]) for r in con.execute("PRAGMA table_info(commands)")}: 
            con.execute("ALTER TABLE commands ADD COLUMN payload_json TEXT")
        payload = {"account_id": account_id, "symbol": args.symbol.upper(), "budget": float(args.budget)}
        con.execute(
            "INSERT INTO commands (action, operator, requested_at, payload_json) VALUES (?, ?, ?, ?)",
            ("activate_asset", "owner_direct", time.time(),
             json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
        con.commit()
        con.close()
    except sqlite3.Error as exc:
        print(f"❌ فشل كتابة طلب البوابة: {exc}")
        return 8
    print("✅ انكتب طلب بدء الأصل في بوابة 901، وليس في جسر MT5 مباشرة.")
    print(f"   {payload['symbol']} · R=${payload['budget']:.2f} · الحساب {account_id}")
    print("التفعيل الفعلي يحتاج النواة شغّالة؛ راقب الزوج المحايد على لوحة القيادة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
