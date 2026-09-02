"""حارس سلسلة الصفقة — حكم المالك ٢٠٢٦-٠٨-١٦.

> «صفقة حقيقيّة بلا قياس ليست اختبارًا، بل حدثًا لا نستطيع إثبات نتيجته.»

السلسلة: قرار ← `request_id` ← أمر ← تأكيد ← حدث ← مخزن ← قارئ ← نتيجة.
والكسور السبعة التي حدّدها المالك تُختبر واحدًا واحدًا.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("trade_chain", ROOT / "shared" / "trade_chain.py")
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)

bad = 0
checked = 0


def show(case: str, ok: bool, detail: str = "") -> None:
    global bad, checked
    checked += 1
    if not ok:
        bad += 1
    print("   %-58s %-24s %s" % (case, detail, "✓" if ok else "✘"))


CMD = {"session_epoch": 1786875024, "request_id": "pair-A-BTCUSD-1786875024-2-buy-a1",
       "pair_id": "pair-A-BTCUSD-1786875024-2", "side": "BUY",
       "requested_price": 62933.71, "volume": 0.29}
CONF = {**CMD, "executed_price": 62939.30}
WHOLE = {name: True for name in T.LINKS}

print("=" * 106)
print("حارس سلسلة الصفقة — ما لا يُثبَت لا يُعَدّ نتيجة")
print("=" * 106)

print("\n١· السلسلة الكاملة:")
v = T.match(CMD, CONF)
show("أمر + تأكيد بنفس الهويّة ⇒ منفَّذ", v["status"] == T.FILLED
     and v["usable"] is True, v["reason"])
s = T.slippage(CMD, CONF)
show("والانزلاق يظهر من الفرق بين المطلوب والمنفَّذ",
     s["measured"] and abs(s["slippage_price"] - 5.59) < 0.01,
     "%+.2f" % s["slippage_price"])
show("شراء نُفّذ أعلى ⇒ ضدّك", s["adverse"] is True, "adverse")
sell = T.slippage({**CMD, "side": "SELL"}, {**CONF, "executed_price": 62927.30})
show("وبيع نُفّذ أدنى ⇒ ضدّك أيضًا",
     sell["adverse"] is True and sell["slippage_price"] > 0,
     "%+.2f" % sell["slippage_price"])
h = T.chain_health(WHOLE)
show("والسلسلة كاملة ⇒ قابلة للإثبات",
     h["healthy"] and h["provable"], h["reason"])

print("\n٢· الكسور السبعة:")

v = T.match(CMD, None)
show("١· لا تأكيد ⇒ لا FILLED أبدًا",
     v["status"] == T.UNCONFIRMED and v["usable"] is False, v["status"])

v = T.match(None, CONF)
show("٢· تأكيد بلا طلب ⇒ يتيم غير قابل للمطابقة",
     v["status"] == T.ORPHAN_CONFIRMATION and v["matched"] is False, v["reason"])

show("٣· طلب مكرّر ⇒ لا صفّ جديد كاذب",
     T.is_duplicate(CONF, [T.identity(CMD)]) is True
     and T.is_duplicate({**CONF, "request_id": "other"}, [T.identity(CMD)]) is False,
     "مكرّر=صحيح · جديد=خطأ")

h = T.chain_health({**WHOLE, "stored": False})
show("٤· حدث موجود والمخزن لم يستقبله ⇒ DEGRADED",
     h["degraded"] and "stored" in h["broken"] and h["provable"] is False,
     h["reason"])

h = T.chain_health({**WHOLE, "read": False})
show("٥· المخزن يستقبل والقارئ لا يقرأ ⇒ DEGRADED",
     h["degraded"] and "read" in h["broken"] and h["provable"] is False,
     h["reason"])

s = T.slippage(CMD, {**CONF, "executed_price": 63100.0})
show("٦· سعر الطلب موجود والتنفيذ مختلف ⇒ يظهر الانزلاق",
     s["measured"] and s["slippage_price"] > 100, "%+.1f" % s["slippage_price"])

v = T.match(CMD, {**CONF, "session_epoch": 1786999999})
show("٧· إعادة تشغيل بين الأمر والتأكيد ⇒ الهويّة لا تضيع",
     v["status"] == T.IDENTITY_MISMATCH and v["matched"] is False,
     v["reason"])

print("\n٣· الهويّة ليست `request_id` وحده (بعد A₀):")
same_id = {**CMD, "session_epoch": 9999}
show("نفس request_id بجلسة أخرى ⇒ هويّة مختلفة",
     T.identity(CMD) != T.identity(same_id), "لا تصادم")
show("ونفس الجلسة والزوج ⇒ الهويّة نفسها",
     T.identity(CMD) == T.identity({**CMD}), "مطابقة")
show("والزوج جزء من الهويّة",
     T.identity(CMD) != T.identity({**CMD, "pair_id": "other"}), "pair_id يفرّق")

print("\n٤· ما نطلبه ≠ ما تأكّد حدوثه:")
v = T.match(CMD, {**CONF, "volume": 0.10})
show("تنفيذ جزئيّ ⇒ PARTIAL لا FILLED",
     v["status"] == T.PARTIAL and v["filled_volume"] == 0.10,
     "%.2f من %.2f" % (v["filled_volume"], v["requested_volume"]))
s = T.slippage(CMD, {k: val for k, val in CONF.items() if k != "executed_price"})
show("وبلا سعر تنفيذ ⇒ لا انزلاق مخترَع",
     s["measured"] is False and s["usable"] is False, s["reason"])
for missing in ("command", "confirmation", "trade_event"):
    h = T.chain_health({**WHOLE, missing: False})
    show("كسر الوصلة %-14s ⇒ تُسمّى ولا تُبتلع" % missing,
         h["degraded"] and missing in h["broken"], h["reason"])

print("\n" + "=" * 106)
print("الفحوص = %d · الاختلافات = %d" % (checked, bad))
print("سليم: كل تنفيذ يُثبَت أو يُعلَن غير مُثبَت — ولا فجوة صامتة."
      if bad == 0 else "ساقط: وصلة تنكسر بصمت أو تنفيذ يُفترَض.")
sys.exit(1 if bad else 0)
