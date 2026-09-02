#!/usr/bin/env python3
"""فحص تغذية الكريبتو الحيّة (MEXC · Binance · سجلّ الرموز).

درس «متحفٌ يبتسم» (٢٠٢٦-٠٨-٢١): ذرّة قد تعلن «سليمة» وأرقامها متجمّدة عند رقم
الإحماء إلى الأبد. فالفحص **لا يسأل الذرّة عن حالها** — يراقب الأرقام التي
تنشرها عن نفسها ويطالب بأن تتحرّك. الأخضر الساكن ليس نجاحًا.

وفرقٌ لا يجوز الخلط فيه — كلّفني إفشالًا كاذبًا أوّل تشغيل (٢٠٢٦-٠٨-٢٩):
  • **عدّاد تراكميّ** (ticks · candles · oi) يجب أن يزيد.
  • **مستوى** (symbols · connected) رقمٌ ثابت المعنى — مطالبته بالحركة غلط.
ونافذة الانتظار ليست رقمًا من رأسي: تُشتقّ من دورة كل مصدر بمانيفسته
(`interval_ms` و`*_poll_s`)، ويُنتظَر حتى يتحرّك أو تنتهي نافذته — أيّهما أسبق.

قراءة فقط من نواة الكريبتو الحيّة. لا يلمس ذرّة ولا يكتب حرفًا.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

CORE = "http://127.0.0.1:8020"
ATOMS_DIR = Path(__file__).resolve().parents[2] / "atoms_crypto"
POLL_S = 3.0
FLOOR_S, CEIL_S = 12.0, 90.0

# الذرّة → (الاسم، عدّادات تراكميّة يجب أن تزيد، مستويات يجب أن تتجاوز الصفر، إلزاميّة)
WATCH: dict[int, tuple[str, tuple[str, ...], tuple[str, ...], bool]] = {
    2620: ("مصدر MEXC — WebSocket", ("ticks", "trades"), ("connected",), True),
    2621: ("مصدر MEXC — REST", ("candles",), (), True),
    2622: ("مصدر Binance", ("oi",), (), False),
    1002: ("تقنية السوق (سجلّ الرموز)", (), ("symbols",), True),
}


def budget_for(aid: int) -> float:
    """نافذة الانتظار من دورة الذرّة نفسها: ثلاث دورات، بحدّين معلنَين."""
    for mf in ATOMS_DIR.glob("*/%d_*/manifest.yaml" % aid):
        text = mf.read_text(encoding="utf-8-sig")
        cycles = [float(m) / 1000.0 for m in re.findall(r"^\s*interval_ms:\s*(\d+)", text, re.M)]
        cycles += [float(m) for m in re.findall(r"^\s*\w*poll_s:\s*(\d+)", text, re.M)]
        if cycles:
            return max(FLOOR_S, min(CEIL_S, max(cycles) * 3.0))
    return FLOOR_S


def atoms() -> dict[int, dict]:
    with urllib.request.urlopen(CORE + "/api/atoms", timeout=10) as r:
        return {int(a["id"]): a for a in json.loads(r.read().decode("utf-8"))}


def counters(atom: dict | None) -> dict[str, int]:
    """`اسم=رقم` من رسالة الصحّة — الأرقام التي تنشرها الذرّة عن نفسها."""
    msg = ((atom or {}).get("health") or {}).get("message") or ""
    return {k: int(v) for k, v in re.findall(r"([A-Za-z_]+)=(\d+)", msg)}


def main() -> int:
    try:
        base = atoms()
    except Exception as exc:                                    # noqa: BLE001
        print("نواة الكريبتو (8020) غير قابلة للوصول:", exc)
        return 2

    print("ذرّات نواة الكريبتو:", len(base))
    budgets = {aid: budget_for(aid) for aid in WATCH}
    horizon = max(budgets.values())
    print("نافذة المراقبة مشتقّة من دورة كل مصدر — أقصاها %.0f ثانية.\n" % horizon)

    first = {aid: counters(base.get(aid)) for aid in WATCH}
    moved: dict[int, tuple[str, float]] = {}
    started = time.monotonic()
    latest = base

    while True:
        elapsed = time.monotonic() - started
        pending = [a for a in WATCH if a not in moved and WATCH[a][1] and elapsed < budgets[a]]
        if not pending:
            break
        time.sleep(POLL_S)
        try:
            latest = atoms()
        except Exception:                                       # noqa: BLE001
            continue
        now = time.monotonic() - started
        for aid in pending:
            cur = counters(latest.get(aid))
            grew = [k for k in WATCH[aid][1] if cur.get(k, 0) > first[aid].get(k, -1)]
            if grew:
                moved[aid] = ("+".join(grew), now)

    failures = 0
    for aid, (name, cumulative, gauges, required) in WATCH.items():
        atom = latest.get(aid)
        if atom is None:
            print("🛑 #%d %s :: غير محمّلة" % (aid, name) if required
                  else "🟠 #%d %s :: غير محمّلة" % (aid, name))
            failures += int(required)
            continue

        state = atom.get("state")
        cur = counters(atom)
        detail = " · ".join("%s=%d" % (k, cur[k]) for k in sorted(cur)) or "بلا أرقام"

        if state != "running":
            print("%s #%d %s :: واقفة (%s)" % ("🛑" if required else "🟠", aid, name, state))
            failures += int(required)
            continue

        dead = [g for g in gauges if cur.get(g, 0) <= 0]
        if dead:
            print("🛑 #%d %s :: مستوى صفر (%s) — %s" % (aid, name, ",".join(dead), detail))
            failures += int(required)
            continue

        if not cumulative:
            print("🟢 #%d %s :: %s" % (aid, name, detail))
            continue

        if aid in moved:
            grew, secs = moved[aid]
            print("🟢 #%d %s :: تحرّك %s خلال %.0f ثانية — %s" % (aid, name, grew, secs, detail))
        else:
            print("%s #%d %s :: متجمّدة طوال %.0f ثانية (%s) — و«الصحّة» تقول «%s» — %s"
                  % ("🛑" if required else "🟠", aid, name, budgets[aid],
                     ",".join(cumulative), (atom.get("health") or {}).get("state"), detail))
            failures += int(required)

    print("\nالاختلافات = %d" % failures)
    if failures:
        print("🛑 التغذية الحيّة غير سليمة — راجع مصادر MEXC.")
        return 1
    print("🟢 التغذية حيّة فعلًا — كل عدّاد إلزاميّ تحرّك، وكل مستوى فوق الصفر.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
