#!/usr/bin/env python3
"""فحص سلسلة استراتيجيّة الكريبتو — من حواسّ الجلسة حتى بطاقة الإشارة.

السلسلة ثمانِ حلقات؛ إذا سكتت حلقة سكت ما بعدها كلّه. وأصعب ما فيها أنّ
السكوت **قد يكون صحيحًا**: لا مرشّح لأنّ السوق لم يعطِ كسرًا هو نجاح لا عطل.
فالفحص يفصل فصلًا صريحًا:

  🛑 **عطل**   — حلقة غائبة أو واقفة أو منهارة: السلسلة مقطوعة بنيويًّا.
  🟠 **تنتظر** — حلقة حيّة لم يصلها مدخلها بعد: يُعلَن **أين** وقفت، ولا يُفشَل.
  🟢 **تعمل**  — حلقة حيّة أنتجت.

بهذا لا يكذب الفحص في الاتّجاهين: لا يبيع سكوتَ السوق على أنّه عطل، ولا يخبّئ
سلسلةً مقطوعة تحت «كل شيء أخضر».

قراءة فقط من نواة الكريبتو الحيّة. لا يلمس ذرّة ولا يكتب حرفًا.
"""
from __future__ import annotations

import json
import urllib.request

CORE = "http://127.0.0.1:8020"

# (المعرّف، الاسم، دوره في السلسلة)
CHAIN: list[tuple[int, str, str]] = [
    (2151, "حسّ الجلسة — المدى", "يقيس مدى جلسة اليوم"),
    (2152, "حسّ الجلسة — الاتجاه", "يقيس اتجاه جلسة اليوم"),
    (2155, "حسّ الجلسة — السيولة", "يقيس سيولة جلسة اليوم"),
    (2153, "مستويات الأمس", "يحتاج تدوير يوم ليُنتج قمّة/قاع الأمس"),
    (2272, "كسور الأمس (كسر/إعادة اختبار)", "يرفض الشموع قبل وصول المستويات"),
    (2273, "محكمة الزناد", "تؤكّد أو تنقض الكسر"),
    (2274, "مصنّف الدخول", "يحوّل التأكيد إلى مرشّح دخول"),
    (2275, "محرك المخاطر (ميزانيّة + إيقاف يوميّ)", "بوّابة: قد يرفض المرشّح بحقّ"),
    (2276, "مجمّع القرار", "يركّب القرار النهائي"),
    (2277, "بطاقة الإشارة", "المُخرَج الذي يقرأه المالك"),
]

# كلمات الانتظار المشروع التي تنشرها الذرّات عن نفسها (لا تُفشِل)
WAITING = ("AWAITING", "STALE", "NO_CANDIDATE", "WARMUP", "PENDING", "EMPTY")


def main() -> int:
    try:
        with urllib.request.urlopen(CORE + "/api/atoms", timeout=10) as r:
            live = {int(a["id"]): a for a in json.loads(r.read().decode("utf-8"))}
    except Exception as exc:                                    # noqa: BLE001
        print("نواة الكريبتو (8020) غير قابلة للوصول:", exc)
        return 2

    print("ذرّات نواة الكريبتو:", len(live))
    print("حلقات السلسلة المفحوصة:", len(CHAIN), "\n")

    broken, waiting, working = 0, [], 0
    first_stop: str | None = None

    for aid, name, role in CHAIN:
        atom = live.get(aid)
        if atom is None:
            print("🛑 #%d %s :: غير محمّلة — الحلقة مفقودة" % (aid, name))
            broken += 1
            first_stop = first_stop or name
            continue

        state = atom.get("state")
        health = atom.get("health") or {}
        msg = (health.get("message") or "").strip()
        hstate = health.get("state")

        if state != "running":
            print("🛑 #%d %s :: واقفة (%s) — %s" % (aid, name, state, role))
            broken += 1
            first_stop = first_stop or name
            continue

        if hstate == "unhealthy" or state == "failed":
            print("🛑 #%d %s :: منهارة — %s" % (aid, name, msg or atom.get("last_error") or "بلا رسالة"))
            broken += 1
            first_stop = first_stop or name
            continue

        if any(w in msg.upper() for w in WAITING):
            print("🟠 #%d %s :: تنتظر مدخلها — «%s» (%s)" % (aid, name, msg, role))
            waiting.append(name)
            first_stop = first_stop or name
            continue

        print("🟢 #%d %s :: %s" % (aid, name, msg or "حيّة"))
        working += 1

    print("\nتعمل = %d · تنتظر = %d · مقطوعة = %d" % (working, len(waiting), broken))
    print("الاختلافات = %d" % broken)

    if broken:
        print("🛑 السلسلة مقطوعة بنيويًّا عند: %s" % first_stop)
        return 1
    if waiting:
        print("🟠 السلسلة سليمة بنيويًّا وواقفة عند «%s» بانتظار مدخلها." % first_stop)
        print("   هذا ليس عطلًا: سكوت السوق يوقف السلسلة بحقّ. راقب هذه الحلقة.")
        return 0
    print("🟢 السلسلة كاملة تعمل — من حواسّ الجلسة حتى بطاقة الإشارة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
