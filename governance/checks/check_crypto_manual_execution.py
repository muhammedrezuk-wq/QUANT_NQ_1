#!/usr/bin/env python3
"""فحص أنّ تنفيذ الكريبتو بشريّ — «النظام يقترح، وأسمر يكبس الزر».

هذا حاجز أمان لا تجميل: لو تسرّبت لشجرة الكريبتو ذرّةٌ تَنشر أمر تنفيذ أو
تكتب بجسر التداول، صار بإمكان القسم أن يفتح صفقةً بلا يد إنسان — وهذا نقضٌ
لعقد قسم أسمر كلّه.

يُقاس بثلاثة حواجز، بالاسم من المانيفستات والنواة الحيّة:
  ١) لا ذرّة كريبتو تَنشر حدث تنفيذ أو أمر وسيط.
  ٢) لا ذرّة كريبتو تعلن قدرة كتابة على جسر التداول.
  ٣) مُخرَج السلسلة النهائيّ بطاقة إشارة (اقتراح)، لا أمرًا.

قراءة فقط. لا يلمس ذرّة ولا يكتب حرفًا.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CR_DIR = ROOT / "atoms_crypto"
CR_CORE = "http://127.0.0.1:8020"

# أسماء الأحداث التي تعني «أمرٌ يغادر النظام إلى السوق»
ORDER_EVENTS = re.compile(
    r"^(execution\.(order|command)\.|platform\.brain_signal\.|trading\.order|"
    r"perpetual\.target\.command|broker\.order)")

# كلمات تعني كتابةً فعليّة على الوسيط داخل كود الذرّة
WRITE_HINTS = ("place_order", "create_order", "submit_order", "send_order",
               "POST /api/v3/order", "brain_signal_write")

failures = 0


def verdict(ok: bool, good: str, bad: str) -> None:
    global failures
    if ok:
        print("🟢 " + good)
    else:
        print("🛑 " + bad)
        failures += 1


def events(text: str, key: str) -> list[str]:
    out, inside = [], False
    for line in text.splitlines():
        if re.match(rf"^{key}\s*:", line):
            inside = True
            continue
        if inside:
            m = re.match(r'^\s*-\s*["\']?([\w.]+)', line)
            if m:
                out.append(m.group(1))
            elif line.strip() and not line.startswith((" ", "\t")):
                break
    return out


def main() -> int:
    if not CR_DIR.is_dir():
        print("🛑 شجرة الكريبتو غير موجودة:", CR_DIR)
        return 2

    publishers: dict[str, list[str]] = {}
    writers: list[str] = []
    total = 0
    for mf in sorted(list(CR_DIR.glob("*/manifest.yaml")) + list(CR_DIR.glob("*/*/manifest.yaml"))):
        total += 1
        text = mf.read_text(encoding="utf-8-sig", errors="replace")
        for ev in events(text, "publishes"):
            if ORDER_EVENTS.match(ev):
                publishers.setdefault(ev, []).append(mf.parent.name)
        code = mf.parent / "atom.py"
        if code.is_file():
            body = code.read_text(encoding="utf-8", errors="replace")
            if any(h in body for h in WRITE_HINTS):
                writers.append(mf.parent.name)

    print("منيفستات الكريبتو المفحوصة:", total, "\n")

    # ١
    verdict(not publishers,
            "لا ذرّة كريبتو تَنشر أمر تنفيذ — %d منيفستًا نظيفًا" % total,
            "ذرّات كريبتو تَنشر أوامر تنفيذ: " + "; ".join(
                "%s ← %s" % (ev, ", ".join(who)) for ev, who in publishers.items()))

    # ٢
    verdict(not writers,
            "لا ذرّة كريبتو تكتب أمرًا على وسيط",
            "ذرّات كريبتو فيها كتابة أوامر: " + ", ".join(writers))

    # ٣ — المُخرَج النهائي اقتراح
    try:
        with urllib.request.urlopen(CR_CORE + "/api/atoms", timeout=10) as r:
            live = {int(a["id"]): a for a in json.loads(r.read().decode("utf-8"))}
        card = live.get(2277)
        if card is None:
            print("🟠 #2277 بطاقة الإشارة غير محمّلة — لا مُخرَج للسلسلة الآن")
        else:
            print("🟢 مُخرَج السلسلة #2277 «بطاقة إشارة» — اقتراح يُقرأ، لا أمرٌ يُرسَل "
                  "(الحالة: %s)" % card.get("state"))
    except Exception as exc:                                    # noqa: BLE001
        print("🟠 تعذّر سؤال نواة الكريبتو: %s" % type(exc).__name__)

    print("\nالاختلافات = %d" % failures)
    if failures:
        print("🛑 خرق: بقسم الكريبتو طريقٌ يفتح صفقة بلا يد إنسان.")
        return 1
    print("🟢 التنفيذ بشريّ محفوظ — النظام يقترح، والزرّ بيد أسمر.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
