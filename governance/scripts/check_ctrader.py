#!/usr/bin/env python3
"""فحص قراءة فقط لجسر cTrader.

لا يكتب ملف cTrader ولا يرسل أمرًا. يفحص وجود JSONL وعدد tick/spec/depth/heartbeat
حتى نميّز بين مشكلة cBot ومشكلة النواة.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

DEFAULT_PATH = Path(
    r"C:\Users\NQ\Documents\cTrader\User Files\quant_nq_bridge\ctrader_bridge.jsonl"
)


def main() -> int:
    path = Path(os.environ.get("NQ_CTRADER_FEED", "").strip() or DEFAULT_PATH)
    print("فحص جسر cTrader — قراءة فقط")
    print(f"الملف: {path}")
    if not path.is_file():
        print("❌ الملف غير موجود. شغّل QuantNQ_Feed على BTCUSD واتركه يعمل.")
        return 2

    counts: Counter[str] = Counter()
    symbols: Counter[str] = Counter()
    invalid = 0
    last_lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                last_lines.append(line)
                last_lines = last_lines[-5:]
                try:
                    payload = json.loads(line)
                except (TypeError, ValueError):
                    invalid += 1
                    continue
                if not isinstance(payload, dict):
                    invalid += 1
                    continue
                event_type = str(payload.get("t") or "unknown")
                counts[event_type] += 1
                if payload.get("s"):
                    symbols[str(payload["s"])] += 1
    except OSError as exc:
        print(f"❌ تعذّرت قراءة الملف: {exc}")
        return 3

    print(f"الحجم: {path.stat().st_size} بايت")
    print(
        "الأحداث: "
        f"tick={counts['tick']} spec={counts['spec']} depth={counts['depth']} "
        f"heartbeat={counts['hb']} start={counts['start']} stop={counts['stop']} "
        f"غير_صالحة={invalid}"
    )
    print(f"الرموز: {', '.join(symbols) if symbols else '—'}")
    if last_lines:
        print("آخر سطر:")
        print(last_lines[-1])
    if counts["tick"] == 0:
        print("⚠ الملف موجود لكن لا يوجد tick صالح — 622 لن يعلن مرجعًا صالحًا.")
        return 4
    print("✅ يوجد tick cTrader؛ الآن افحص #622 ثم #521 من Check_Health.bat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
