# QUANT_NQ - live read-only probe.
#
# لماذا يعيش هذا الملفّ بالمشروع (٢٠٢٦-٠٨-٢٠، بختم NQ):
#   المالك: «أنا ما بامن بي ورق أو جلسة قرات، أنا بامن بالكود بس — كل ورق يكذب».
#   ⇒ الأثر الدائم ليس الرقم المكتوب بورقة، بل السكربت الذي يعيد استخراجه.
#   كل رقم بقسم «القياس الحيّ» في «٩٠ الباقي» صادر عن هذا الملفّ حرفيًّا،
#   ويُعاد فحصه بلصقة واحدة — فلا يتحوّل الرقم إلى كذبة حين يتقادم.
#
# ⛔ لا يكتب شيئًا. كل قاعدة تُفتح mode=ro. لا يُنشئ ملفًّا ولا يعدّل ولا يحذف.
#
# التشغيل:
#   & "C:\Users\NQ\AppData\Local\Programs\Python\Python312\python.exe" `
#     "C:\Users\NQ\QUANT_NQ\governance\scripts\live_probe.py"
#
# المخرَجات ASCII عمدًا — ترميز طرفيّة ويندوز يشوّه العربي.

import datetime
import glob
import os
import sqlite3
import urllib.request

ROOT = r"C:\Users\NQ\QUANT_NQ"
BRIDGE = r"C:\Users\NQ\AppData\Roaming\MetaQuotes\Terminal\Common\Files\nq_brain.db"


def line(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


def ro(path):
    return sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)


def tables(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


print("=" * 62)
print("QUANT_NQ live probe   %s" % datetime.datetime.now().isoformat(timespec="seconds"))
print("READ-ONLY. Nothing is written.")
print("=" * 62)


# ---------------------------------------------------------------- 1. core alive
line("1. CORE ALIVE?  (the running core, not the code on disk)")
for url in ("http://127.0.0.1:8010/health",
            "http://127.0.0.1:8010/api/health",
            "http://127.0.0.1:8090/gov/version"):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            print("OK   %-40s -> %s" % (url, resp.read(400).decode("utf-8", "replace")))
    except Exception as exc:
        print("DEAD %-40s -> %s: %s" % (url, type(exc).__name__, exc))


# ------------------------------------------------- 2. governed dials: approved?
line("2. DECISION DIALS - is any value APPROVED by owner? (min_score etc.)")
found = False
for path in glob.glob(os.path.join(ROOT, "var", "store", "*.db")) + \
            glob.glob(os.path.join(ROOT, "**", "parameters*.db"), recursive=True):
    try:
        conn = ro(path)
        if "parameters" not in tables(conn):
            conn.close()
            continue
        found = True
        print("\n-- %s" % path)
        for row in conn.execute(
                "SELECT name,value,status,source,version FROM parameters ORDER BY name"):
            print("   %-28s value=%-10s status=%-12s src=%-8s v=%s" % row)
        conn.close()
    except Exception as exc:
        print("   ERR %s -> %s" % (path, exc))
if not found:
    print("   no 'parameters' table found in any var/store/*.db")


# ------------------------------------------- 3. THE MONEY QUESTION: cost signs
line("3. COST SIGNS - are commission/swap/fee NEGATIVE?")
print("   517 computes  net = gross + commission + swap + fee")
print("   POSITIVE  -> net inflated silently.")
print("   0.0       -> 517 reads it as KNOWN (0.0 is not None) and reports")
print("                costs_complete=True with cost_total=0.00  <-- band 90-33\n")
try:
    conn = ro(BRIDGE)
    tbls = tables(conn)
    print("   bridge tables: %s\n" % ", ".join(tbls))
    for tname in tbls:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % tname)]
        money = [c for c in cols if c.lower() in
                 ("profit", "commission", "swap", "fee", "entry_price", "volume")]
        if len(money) < 2:
            continue
        print("   -- %s  (%d rows)" % (
            tname, conn.execute("SELECT COUNT(*) FROM %s" % tname).fetchone()[0]))
        sel = ",".join(money)
        for row in conn.execute(
                "SELECT %s FROM %s ORDER BY rowid DESC LIMIT 8" % (sel, tname)):
            print("      " + "  ".join("%s=%s" % (c, v) for c, v in zip(money, row)))
        print("")
    conn.close()
except Exception as exc:
    print("   ERR bridge -> %s: %s" % (type(exc).__name__, exc))


# ------------------------------------------------------- 4. store inventory
line("4. STORE INVENTORY - which .db actually has rows?")
paths = sorted(glob.glob(os.path.join(ROOT, "var", "store", "*.db")))
print("   %d files in var/store\n" % len(paths))
for path in paths:
    try:
        conn = ro(path)
        total = 0
        for tname in tables(conn):
            total += conn.execute("SELECT COUNT(*) FROM %s" % tname).fetchone()[0]
        conn.close()
        print("   %-46s %10d rows  %10.1f KB" % (
            os.path.basename(path), total, os.path.getsize(path) / 1024.0))
    except Exception as exc:
        print("   %-46s ERR %s" % (os.path.basename(path), exc))


# -------------------------------------------------- 5. shadow db still there?
line("5. SHADOW DB - the one nobody reads (paper 99/4)")
shadow = os.path.join(ROOT, "shared", "var", "store")
if os.path.isdir(shadow):
    for path in sorted(glob.glob(os.path.join(shadow, "*.db"))):
        print("   EXISTS  %s  (%.1f KB)" % (path, os.path.getsize(path) / 1024.0))
else:
    print("   clean - no shared/var/store directory")

print("\n" + "=" * 62)
print("DONE. Nothing was written. Every DB was opened mode=ro.")
print("=" * 62)
