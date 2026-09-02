"""حارس عقد فكّ التحوّط — نصّه في `٢٥ §٨` بحكم المالك ٢٠٢٦-٠٨-١٦.

> «لا انتقال إلى `DIRECTIONAL` بناءً على النيّة أو الطلب.
>  الانتقال لا يحدث إلا بعد إثبات حالة التنفيذ الفعليّة.»

حارس سلوكيّ: يشغّل آلة الحالات نفسها. كل انتقال صحيح يُختبر، ثمّ **يُكسر كل
شرط على حدة** ويجب أن يُمنع الانتقال — لا أن يُسجَّل ويمضي.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

FOLDER = next(d for d in (ATOM_ROOT).iterdir()
              if d.is_dir() and d.name.startswith("578_"))
spec = importlib.util.spec_from_file_location("unhedge", FOLDER / "unhedge.py")
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)

bad = 0
checked = 0


def show(case: str, ok: bool, detail: str = "") -> None:
    global bad, checked
    checked += 1
    if not ok:
        bad += 1
    print("   %-56s %-26s %s" % (case, detail, "✓" if ok else "✘"))


def healthy() -> dict:
    """كل البوّابات مفتوحة — الحالة التي يجوز فيها الفكّ."""
    return {name: (False if name in U._INVERTED else True) for name, _ in U.GATES} | {
        "unhedge_signal": True, "net_matches_target": True}


OPEN = {"pair_id": "pair-A-BTCUSD-1786-1", "cycle_id": "BTCUSD|60s|1",
        "status": U.STATUS_OPEN, "hedge_target": 0.0, "max_attempts": 3,
        "legs": {"BUY": {"status": "RECONCILED"}, "SELL": {"status": "RECONCILED"}}}


print("=" * 104)
print("حارس عقد فكّ التحوّط — الانتقال بالإثبات لا بالنيّة")
print("=" * 104)

# ── ١· الانتقالات الصحيحة ───────────────────────────────────────────────────
print("\n١· الانتقالات الصحيحة:")
v = U.decide(dict(OPEN), healthy())
show("زوج مفتوح + إشارة فكّ + بوّابات سليمة ⇒ يبدأ",
     v["status"] == U.STATUS_UNHEDGING and v["action"] == "BEGIN_UNHEDGE",
     v["status"])

mid = dict(OPEN, status=U.STATUS_UNHEDGING)
v = U.decide(dict(mid), healthy())
show("رجلان مطابقتان + الصافي بلغ الهدف ⇒ اتّجاهيّ",
     v["status"] == U.STATUS_DIRECTIONAL and v["action"] == "COMPLETE_UNHEDGE",
     v["status"])
show("والهويّة تُحفظ — لا زوج جديد ولا ميزانيّة جديدة",
     v["pair_id"] == OPEN["pair_id"] and v["cycle_id"] == OPEN["cycle_id"],
     str(v["pair_id"])[-12:])

# ── ٢· بلا إشارة صريحة لا يتحرّك شيء ───────────────────────────────────────
print("\n٢· الفكّ لا يقع تلقائيًّا:")
v = U.decide(dict(OPEN), dict(healthy(), unhedge_signal=False))
show("بوّابات سليمة وبلا إشارة ⇒ لا فكّ",
     v["status"] == U.STATUS_OPEN and v["action"] == "NONE", v["action"])
v = U.decide(dict(OPEN), {k: val for k, val in healthy().items()
                          if k != "unhedge_signal"})
show("إشارة غائبة (لا مفتاح أصلًا) ⇒ لا فكّ",
     v["status"] == U.STATUS_OPEN, v["status"])

# ── ٣· كسر كل بوّابة على حدة ────────────────────────────────────────────────
print("\n٣· كسر كل شرط سلامة على حدة — كلّها يجب أن تمنع البدء:")
for name, reason in U.GATES:
    state = healthy()
    state[name] = True if name in U._INVERTED else False
    v = U.decide(dict(OPEN), state)
    ok = (v["status"] == U.STATUS_OPEN and v["execution_blocked"] is True
          and v["block_reason"] == reason and v["action"] == "NONE")
    show("كسر %-24s ⇒ يُمنع بسبب مُسمّى" % name, ok, v["block_reason"] or "مرّ!")

print("\n   وغياب المفتاح ليس إذنًا (fail-closed) — كشفه كسر متعمّد على الحارس نفسه:")
for name, reason in U.GATES:
    if name in U._INVERTED:
        continue                      # المقلوب غيابه يعني «لا قاطع» وهو الصحيح
    state = {k: v for k, v in healthy().items() if k != name}
    v = U.decide(dict(OPEN), state)
    ok = (v["execution_blocked"] is True and v["block_reason"] == reason
          and v["status"] == U.STATUS_OPEN)
    show("غياب %-24s ⇒ يُمنع لا يُقرأ إذنًا" % name, ok, v["block_reason"] or "مرّ!")

print("\n   والتعليق لا يغلق الرجل الأخرى:")
state = healthy(); state["account_fresh"] = False
v = U.decide(dict(OPEN), state)
show("مُنع الفكّ ⇒ لا أمر إغلاق ولا تجميد",
     v["retry_leg"] is None and v["freeze"] is False and v["action"] == "NONE",
     "action=%s" % v["action"])

# ── ٤· القاعدة الذهبيّة ─────────────────────────────────────────────────────
print("\n٤· القاعدة الذهبيّة — لا انتقال بالنيّة:")
for label, legs, net in (
        ("رجل واحدة لم تُطابَق بعد",
         {"BUY": {"status": "RECONCILED"}, "SELL": {"status": "REQUESTED"}}, True),
        ("الرجلان مطابقتان والصافي لم يبلغ الهدف",
         {"BUY": {"status": "RECONCILED"}, "SELL": {"status": "RECONCILED"}}, False),
        ("لا أرجل أصلًا", {}, True)):
    v = U.decide(dict(OPEN, status=U.STATUS_UNHEDGING, legs=legs),
                 dict(healthy(), net_matches_target=net))
    show("%-44s ⇒ يبقى UNHEDGING" % label,
         v["status"] == U.STATUS_UNHEDGING, v["status"])

# ── ٥· الرجل الفاشلة — عقد ٥٨ ───────────────────────────────────────────────
print("\n٥· الرجل الفاشلة تُعاد وحدها (عقد ٥٨):")
legs = {"BUY": {"status": "RECONCILED"}, "SELL": {"status": "FAILED", "attempts": 1}}
v = U.decide(dict(OPEN, status=U.STATUS_UNHEDGING, legs=legs), healthy())
show("تُعاد الفاشلة وحدها بنفس الهويّة",
     v["action"] == "RETRY_LEG" and v["retry_leg"] == "SELL"
     and v["pair_id"] == OPEN["pair_id"], "%s" % v["retry_leg"])
show("ولا تُغلق الناجحة ولا يقع CLOSE_ALL",
     v["freeze"] is False and v["status"] == U.STATUS_UNHEDGING, v["status"])

legs = {"BUY": {"status": "RECONCILED"}, "SELL": {"status": "FAILED", "attempts": 3}}
v = U.decide(dict(OPEN, status=U.STATUS_UNHEDGING, legs=legs), healthy())
show("نفاد المحاولات ⇒ تصعيد وتجميد لا تخمين",
     v["status"] == U.STATUS_EXHAUSTED and v["freeze"] is True
     and v["action"] == "ESCALATE", v["block_reason"] or "")

legs = {"BUY": {"status": "RECONCILED"}, "SELL": {"status": "FAILED", "attempts": 1}}
v = U.decide(dict(OPEN, status=U.STATUS_UNHEDGING, legs=legs),
             dict(healthy(), spread_valid=False))
show("وإعادة المحاولة نفسها محكومة بالبوّابات",
     v["execution_blocked"] is True and v["retry_leg"] is None,
     v["block_reason"] or "مرّت!")

# ── ٦· `H` هدف من `S` لا نسبة أحجام ────────────────────────────────────────
print("\n٦· `H` يبقى هدفًا مشتقًّا من `S`:")
v = U.decide(dict(OPEN, hedge_target=0.35), healthy())
show("الآلة تنقل هدف التحوّط ولا تشتقّه من الرجلين",
     v["hedge_target"] == 0.35, "H=%s" % v["hedge_target"])

print("\n" + "=" * 104)
print("الفحوص = %d · الاختلافات = %d" % (checked, bad))
print("سليم: الفكّ لا يقع إلا بشرط صريح، ولا يكتمل إلا بإثبات تنفيذ."
      if bad == 0 else "ساقط: انتقال يقع بلا إثبات أو شرط لا يمنع.")
sys.exit(1 if bad else 0)
