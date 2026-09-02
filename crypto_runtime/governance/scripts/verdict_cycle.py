"""حكم الدورة التكامليّة — العلامات التسع التي حدّدها المالك ٢٠٢٦-٠٨-١٦.

قياس فقط: لا يكتب حرفًا ولا يلمس ذرّة. يُشغَّل بعد الدورة النظيفة وبعد
إعادة التشغيل، ويُقارَن الخرجان.

    py governance\\scripts\\verdict_cycle.py            # قبل إعادة التشغيل
    py governance\\scripts\\verdict_cycle.py --after    # بعدها

القاعدة الحاكمة: `0` مخالفات ليس إثباتًا سلوكيًّا. وأمرٌ أُرسل ليس صفقةً
نجحت — بلا تأكيد تبقى `UNCONFIRMED`.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
BRIDGE = pathlib.Path(
    r"C:\Users\NQ\AppData\Roaming\MetaQuotes\Terminal\Common\Files\nq_brain.db")
SNAPS = ROOT / "var" / "snapshots"
STATE = ROOT / "var" / "governance" / "cycle_verdict.json"

marks: list[tuple[str, bool | None, str]] = []


def mark(name: str, ok: bool | None, detail: str) -> None:
    marks.append((name, ok, detail))


def atoms() -> dict[int, dict]:
    try:
        rows = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:8010/api/atoms", timeout=10).read())
        return {r["id"]: r for r in rows}
    except Exception:                                  # noqa: BLE001
        return {}


def msg(live: dict, atom_id: int) -> str:
    return ((live.get(atom_id) or {}).get("health") or {}).get("message") or ""


def bridge_rows(sql: str) -> list[sqlite3.Row]:
    if not BRIDGE.exists():
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % BRIDGE, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


live = atoms()
after = "--after" in sys.argv
previous = {}
if STATE.exists():
    try:
        previous = json.loads(STATE.read_text(encoding="utf-8"))
    except ValueError:
        previous = {}

# ١· الهويّة — يُفصَل القديم عن الجديد.
# معرّف ما بعد A₀ يحمل ختم الجلسة: pair-<acct>-<sym>-<epoch>-<n>-<side>-aN.
# تكرار معرّف من صيغة قديمة أثرٌ تاريخيّ ولا يثبت فشل الإصلاح الحاليّ.
def stamped(request_id: str) -> bool:
    for part in str(request_id).split("-"):
        if part.isdigit() and int(part) > 1_000_000_000:
            return True
    return False


dupes = bridge_rows("SELECT request_id, COUNT(*) n FROM commands "
                    "WHERE request_id LIKE 'pair-%' GROUP BY request_id "
                    "HAVING n > 1")
old = [r for r in dupes if not stamped(r["request_id"])]
current = [r for r in dupes if stamped(r["request_id"])]
mark("الهويّة: لا تصادم في الجلسة الحاليّة", not current,
     "حاليّ=%d · تاريخيّ=%d" % (len(current), len(old)))
if old:
    mark("  (أثر تاريخيّ قبل A₀ — لا يُحسب فشلًا)", None,
         "أقدم صيغة=%d" % len(old))

# ٢· التنفيذ — ما طُلب ≠ ما تأكّد
pairs = bridge_rows(
    "SELECT c.request_id, c.price req, t.entry_price fill FROM commands c "
    "LEFT JOIN trade_events t ON t.request_id = c.request_id "
    "AND t.event_type='OPENED' WHERE c.action='OPEN' ORDER BY c.id DESC LIMIT 6")
differing = [r for r in pairs if r["fill"] is not None
             and abs((r["fill"] or 0) - (r["req"] or 0)) > 1e-9]
mark("التنفيذ: أمر ≠ تأكيد (سعران مختلفان)", bool(differing) if pairs else None,
     "%d من %d" % (len(differing), len(pairs)))

# ٣· بلا تأكيد تبقى UNCONFIRMED
unconfirmed = [r for r in pairs if r["fill"] is None]
mark("التأكيد: أمر بلا تنفيذ يبقى غير مؤكَّد", True,
     "غير مؤكَّد=%d" % len(unconfirmed))

# ٤· الانزلاق
slip = msg(live, 563)
mark("الانزلاق: 563 يقيس كل تنفيذ قابل للمطابقة",
     ("slip=" in slip and "NO_REQUESTED_PRICE" not in slip) if slip else None,
     slip or "النواة صامتة")

# ٥· الصافي — المجهول لا يصير صفرًا
costs = bridge_rows("SELECT commission, swap, fee FROM trade_events "
                    "WHERE event_type IN ('CLOSED','PARTIAL') "
                    "ORDER BY id DESC LIMIT 1")
if costs:
    row = costs[0]
    known = [k for k in ("commission", "swap", "fee") if row[k] is not None]
    mark("الصافي: التكاليف مقروءة من الوسيط", len(known) == 3,
         "معلوم=%s" % (",".join(known) or "لا شيء"))
else:
    mark("الصافي: التكاليف مقروءة من الوسيط", None, "لا إغلاق بعد")

# ٦· حالة الزوج
pair_msg = msg(live, 578)
mark("الزوج: حالته سليمة", bool(pair_msg) and "EXHAUSTED" not in pair_msg,
     pair_msg or "النواة صامتة")

# ٧· اللقطة تُكتب عند الإيقاف النظيف
files = [f for f in SNAPS.rglob("*") if f.is_file()] if SNAPS.exists() else []
mark("اللقطة: مكتوبة على القرص", bool(files), "ملفّات=%d" % len(files))

# ٨· إعادة التشغيل — الحالة نفسها.
# المصدر هو ملفّات اللقطات على القرص، لا رسائل الصحّة: الـAPI لا يعيد
# التفاصيل، فالمقارنة عبرها كانت ستقارن `None` بـ`None` وتبدو ناجحة.
def snap_of(atom_id: int) -> dict:
    path = SNAPS / ("%d.json" % atom_id)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def body_of(atom_id: int) -> dict:
    raw = snap_of(atom_id)
    return raw.get("payload") if isinstance(raw.get("payload"), dict) else raw


now_state = {
    "counter_578": body_of(578).get("counter"),
    "pairs_578": sorted((body_of(578).get("pairs") or {}).keys()),
    "active_576": sorted(body_of(576).get("active") or []),
    "counter_576": body_of(576).get("counter"),
    "last_success_714": body_of(714).get("last_success"),
}

if after and previous:
    before = previous.get("state") or {}
    c_before, c_after = before.get("counter_578"), now_state["counter_578"]
    mark("العدّاد: لم يرجع للصفر",
         c_after is not None and c_before is not None and c_after >= c_before,
         "قبل=%s بعد=%s" % (c_before, c_after))
    mark("الزوج: نفس الهويّة والحالة",
         before.get("pairs_578") == now_state["pairs_578"]
         and before.get("active_576") == now_state["active_576"],
         "أزواج=%d أصول=%d" % (len(now_state["pairs_578"]),
                               len(now_state["active_576"])))
    ls_b, ls_a = before.get("last_success_714"), now_state["last_success_714"]
    mark("الأرشفة: last_success محفوظ ولا أرشفة مكرّرة",
         ls_a is not None and ls_b is not None and ls_a >= ls_b,
         "قبل=%s بعد=%s" % (ls_b, ls_a))
else:
    mark("إعادة التشغيل: يُقارَن بعد --after", None,
         "عدّاد=%s أزواج=%d" % (now_state["counter_578"],
                                len(now_state["pairs_578"])))

# ٩· الأرشفة — تُفصَل «لم تعمل قطّ» عن «قرّرت ألّا تعمل».
# الاثنان كانا يُطبعان معًا فبدا الأمر تناقضًا: NEVER_RAN SKIPPED.
arch = msg(live, 714)
if not arch:
    mark("الأرشفة: حالتها معلنة بلا لبس", None, "النواة صامتة")
elif "SKIPPED" in arch or "WITHIN_WINDOW" in arch:
    mark("الأرشفة: داخل النافذة ⇒ لا حاجة للتشغيل", True, "SKIPPED_WINDOW")
elif "CATCHUP" in arch:
    mark("الأرشفة: لحاق مطلوب وقد وقع", True, "CATCHUP")
elif "NEVER_RAN" in arch or "AWAITING_FIRST_PULSE" in arch:
    mark("الأرشفة: لم تعمل ولم تقرّر — لحاق لم يُحسم", False, "لا قرار")
elif "runs=" in arch and "archived" in arch:
    mark("الأرشفة: عملت ونجحت", True, arch[:26])
else:
    mark("الأرشفة: حالتها معلنة بلا لبس", None, arch[:26])

print("=" * 96)
print("حكم الدورة التكامليّة — %s" % ("بعد إعادة التشغيل" if after else "قبل إعادة التشغيل"))
print("=" * 96)
ok = sum(1 for _, v, _ in marks if v is True)
fail = sum(1 for _, v, _ in marks if v is False)
unknown = sum(1 for _, v, _ in marks if v is None)
for name, value, detail in marks:
    sign = "✓" if value is True else "✘" if value is False else "—"
    print("   %-46s %-28s %s" % (name, detail[:28], sign))

STATE.parent.mkdir(parents=True, exist_ok=True)
if not after:
    # يُكتب قبل إعادة التشغيل فقط، فلا تمحو الجولةُ الثانية مرجعَ المقارنة.
    STATE.write_text(json.dumps({"state": now_state, "pair_msg": pair_msg,
                                 "slip": slip}, ensure_ascii=False),
                     encoding="utf-8")

print("-" * 96)
print("ناجح=%d · ساقط=%d · غير محسوم=%d" % (ok, fail, unknown))
print("الحكم: %s" % ("الدورة مثبتة" if fail == 0 and unknown == 0
                     else "لم تُحسم بعد" if fail == 0 else "ساقطة"))
sys.exit(1 if fail else 0)
