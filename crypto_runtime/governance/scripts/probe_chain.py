#!/usr/bin/env python3
"""
scripts/probe_chain.py — مسبار سلسلة القرار من اللوحة الحيّة
=============================================================
يقرأ `GET /state` من 850 ويطبع محطات الخطّ بالترتيب: من نبض المنصّة إلى
الأمر المكتوب — عمرَ كل محطة، وأول محطة ميتة هي **نقطة الانسداد** بلا
تخمين.

    python scripts\\probe_chain.py            # الافتراضي 127.0.0.1:8850
    python scripts\\probe_chain.py --port 8850
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

MILESTONES: list[tuple[str, str]] = [
    ("نبض المنصّة",        "platform.terminal_state"),
    ("سوق موحّد",          "market_data.unified"),
    ("تحليل",              "analysis.unified"),
    ("بنية السوق",         "structure.family_status"),
    ("سيولة",              "liquidity.section_summary"),
    ("إحصاء",              "stats.unified"),
    ("احتمالات",           "probability.family_status"),
    ("استراتيجيات",        "strategy.unified"),
    ("قرار مُراجَع",        "decision.reviewed"),
    ("قرار معتمد",         "decision.approved"),
    ("مخاطر",              "risk.unified"),
    ("تنفيذ",              "execution.unified"),
    ("مراكز المنصّة",       "platform.positions.state"),
    ("إدارة الصفقة",        "execution.management.unified"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8850)
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/state"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            state = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"تعذّر الوصول إلى {url}: {exc}", file=sys.stderr)
        print("هل النظام يعمل؟ وهل ذرّة 850 على هذا المنفذ؟", file=sys.stderr)
        return 2

    events: dict = state.get("events", {})
    print(f"لقطة اللوحة: حي={state.get('live')} من {state.get('total')} حدثًا متوقَّعًا\n")
    print(f"{'المحطة':<16} {'الحدث':<34} {'الحال'}")
    print("─" * 72)

    first_dead: str | None = None
    for label, name in MILESTONES:
        info = events.get(name)
        if info is None:
            mark = "✗ لم يصل قطّ"
            if first_dead is None:
                first_dead = f"{label} ({name})"
        else:
            age = info.get("age_s")
            age_txt = "—" if age is None else f"{age:.1f}ث"
            src = info.get("source", "؟")
            mark = f"✓ منذ {age_txt}  (من {src})"
        print(f"{label:<16} {name:<34} {mark}")

    # إشارات الاستراتيجيات الخام إن مرّت على اللوحة
    strat = sorted(k for k in events if k.startswith("strategy."))
    if strat:
        print("\nأحداث strategy.* المرصودة:", ", ".join(strat))

    print("\n" + "═" * 72)
    if first_dead is None:
        print("الخطّ حيّ من أوله لآخره — إن غابت الصفقات فالسبب شروط "
              "القرار (عتبات/جلسة/مخاطر) لا انسدادٌ في الأنبوب.")
    else:
        print(f"⛔ نقطة الانسداد: أول محطة ميتة هي «{first_dead}».")
        print("كل ما قبلها بريء، وكل ما بعدها جائع بسببها — ابدأ التحقيق هناك.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
