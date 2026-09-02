"""حارس ختم حالة الحساب — حكم المالك ٢٠٢٦-٠٨-١٥.

العطل المشخَّص حيًّا: `PumpAccount()` في الإكسبرت يرجع مبكّرًا حين لا تتغيّر
`balance/equity/margin/free_margin`، فيبتلع معها ختم `updated_at`. وبما أنّ
الحساب بلا مراكز ولا تداول، بقيت الأرقام متطابقة حرفيًّا ١٠ مرّات في الثانية
ولم يُكتب الختم ٦٨.٧ ساعة. النتيجة: `619` يحسب `age_s` هائلًا و`585` يرفض
التنفيذ بـ`ACCOUNT_STATE_STALE` على بيانات هي في الحقيقة صحيحة.

ثلاثة شقوق:
  ١· المصدر — على مسار «لم يتغيّر شيء» يجب أن يبقى ختم `updated_at` مكتوبًا.
  ٢· الدلالة — `619` يقيس العمر من `updated_at` وحده. `bridge_beat` يقول
     «الإكسبرت حيّ» لا «الأرقام رُصدت»، والخلط بينهما إصلاح شكليّ يعيد بناء
     الكذبة نفسها. حكم المالك: «لا نستخدم `bridge_beat` بديلًا عن `updated_at`».
  ٣· القياس الحيّ — الإكسبرت حيّ، والختم في الجسر أحدث من الحدّ المعلن.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)
EA = ROOT / "mt5" / "QUANT_NQ.mq5"
A619 = ATOM_ROOT / "619_حالة_الحساب" / "atom.py"
M619 = ATOM_ROOT / "619_حالة_الحساب" / "manifest.yaml"
BRIDGE = pathlib.Path(
    r"C:\Users\NQ\AppData\Roaming\MetaQuotes\Terminal\Common\Files\nq_brain.db")

bad = 0


def show(label: str, ok: bool, detail: str = "") -> None:
    global bad
    if not ok:
        bad += 1
    print("   %-52s %-28s %s" % (label, detail, "✓" if ok else "✘"))


def block(src: str, start: int) -> str:
    """يعيد الجملة أو الكتلة التي تلي القوس المغلق عند `start` (مطابقة أقواس)."""
    i = start
    while i < len(src) and src[i] in " \t\r\n":
        i += 1
    if i < len(src) and src[i] == "{":
        depth = 0
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return src[i:j + 1]
        return src[i:]
    end = src.find(";", i)
    return src[i:end + 1] if end != -1 else src[i:]


def body(src: str, sig: str) -> str:
    k = src.index(sig)
    o = src.index("{", k)
    depth = 0
    for j in range(o, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[o:j + 1]
    return src[o:]


def condition_end(src: str, k: int) -> int:
    """نهاية شرط `if` الذي يبدأ عند `k` — بمطابقة الأقواس لا بأوّل `)`."""
    o = src.index("(", k)
    depth = 0
    for j in range(o, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    return o


print("=" * 98)
print("حارس ختم حالة الحساب (`updated_at`) — الجذر في الإكسبرت لا في بايثون")
print("=" * 98)

# ── ١· المصدر ───────────────────────────────────────────────────────────────
print("\n١· مسار «لم يتغيّر شيء» في `PumpAccount()` لا يبتلع الختم:")
if not EA.exists():
    show("ملف الإكسبرت موجود", False, str(EA))
else:
    src = EA.read_text(encoding="utf-8", errors="replace")
    try:
        fn = body(src, "void PumpAccount()")
    except ValueError:
        fn = ""
        show("`PumpAccount()` موجودة", False, "لم تُعثر")
    if fn:
        show("`PumpAccount()` موجودة", True, "%d حرف" % len(fn))
        guards = [i for i in range(len(fn))
                  if fn.startswith("if", i)
                  and all(t in fn[i:i + 220]
                          for t in ("LastBalance", "LastEquity", "LastMargin", "LastFree"))]
        show("شرط منع الكتابة موجود", len(guards) == 1,
             "عدد=%d" % len(guards))
        if len(guards) == 1:
            seg = block(fn, condition_end(fn, guards[0]))
            show("وفرعه يكتب ختم `updated_at`", "updated_at" in seg,
                 "الفرع=%s" % " ".join(seg.split())[:34])
            # لا يكفي ورود كلمة `account` — `AccountInfoInteger(ACCOUNT_LOGIN)`
            # تحتويها، فيمرّ ختمٌ ذهب إلى جدول آخر. الكسر ٣ أثبت جوف الحاجز.
            target_ok = ("UPDATE account SET" in seg
                         and "updated_at" in seg.split("UPDATE account SET", 1)[1]
                         .split(";", 1)[0])
            show("والختم يستهدف جدول `account`", target_ok,
                 "UPDATE account SET" if target_ok else "جدول آخر أو لا جدول")
            show("ويُنفَّذ فعلًا لا يُكتب فقط",
                 "updated_at" in seg and "DatabaseExecute" in seg,
                 "DatabaseExecute" if "DatabaseExecute" in seg else "لا تنفيذ")
        # والكتابة الثقيلة تبقى مشروطة بالتغيّر — لا نلغي التحسين، نحرّر الختم.
        show("الكتابة الثقيلة ما زالت مشروطة بالتغيّر",
             fn.count("UPDATE account SET balance") == 1,
             "balance=%d" % fn.count("UPDATE account SET balance"))

# ── ٢· الدلالة ──────────────────────────────────────────────────────────────
print("\n٢· `619` يقيس العمر من `updated_at` وحده (لا `bridge_beat`):")
s619 = A619.read_text(encoding="utf-8", errors="replace")
code = "\n".join(l for l in s619.splitlines() if not l.lstrip().startswith("#"))
age_lines = [l for l in code.splitlines() if "age_s" in l and "=" in l and "self._official_time" in l]
show("`age_s` يُشتقّ من `updated_at`",
     bool(age_lines) and any("updated_at" in l for l in age_lines),
     "أسطر=%d" % len(age_lines))
show("ولا يُشتقّ من `bridge_beat`",
     not any("bridge_beat" in l for l in age_lines), "")
show("و`stale` يُقارن بحدّ معلن",
     any("stale" in l and "_max_age_s" in l for l in code.splitlines()), "")
# `bridge_beat` حقل منصّة مشروع، وورودُه سليم. الممنوع أن يدخل حساب العمر أو
# قرار التقادم — فهناك وحده يصير إصلاحًا شكليًّا يكذب على `585`.
contaminated = [l.strip() for l in code.splitlines()
                if "bridge_beat" in l and ("age_s" in l or "stale" in l)]
show("و`bridge_beat` لا يدخل حساب العمر ولا قرار التقادم",
     not contaminated, "أسطر ملوّثة=%d" % len(contaminated))

max_age = 300.0
for line in M619.read_text(encoding="utf-8", errors="replace").splitlines():
    if line.strip().startswith("max_age_s:") and ":" in line:
        try:
            max_age = float(line.split(":", 1)[1].strip())
        except ValueError:
            pass

# ── ٣· القياس الحيّ ─────────────────────────────────────────────────────────
print("\n٣· القياس الحيّ على جسر MT5 (الحدّ المعلن = %.0fs):" % max_age)
if not BRIDGE.exists():
    show("قاعدة الجسر موجودة", False, str(BRIDGE))
else:
    c = sqlite3.connect("file:%s?mode=ro" % BRIDGE, uri=True)
    c.row_factory = sqlite3.Row
    try:
        row = c.execute("SELECT * FROM account LIMIT 1").fetchone()
    finally:
        c.close()
    if row is None:
        show("صفّ الحساب موجود", False, "لا صفّ")
    else:
        now = time.time()
        beat = float(row["bridge_beat"] or 0.0)
        upd = float(row["updated_at"] or 0.0)
        show("الإكسبرت حيّ (نبضة الجسر طازجة)", now - beat <= max_age,
             "عمر النبضة=%.0fs" % (now - beat))
        show("ختم الحساب `updated_at` طازج", now - upd <= max_age,
             "age_s=%.0fs" % (now - upd))
        show("والختم لا يتخلّف عن النبضة", abs(beat - upd) <= max_age,
             "الفارق=%.0fs" % abs(beat - upd))
        print("      الأرقام المرصودة: balance=%s equity=%s free_margin=%s open=%s"
              % (row["balance"], row["equity"], row["free_margin"], row["open_count"]))

print("\n" + "=" * 98)
print("الاختلافات = %d" % bad)
print("سليم: الختم يقول «متى نظرتُ» لا «متى تغيّر الرقم»."
      if bad == 0 else "ساقط: الختم لا يتجدّد — `585` يرفض على بيانات صحيحة.")
sys.exit(1 if bad else 0)
