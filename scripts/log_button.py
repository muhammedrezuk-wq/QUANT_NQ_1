"""سجلّ تشغيل أزرار المشروع دون تسجيل أي سر أو قيمة بيئة."""
from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "docs" / "docs" / "١١_المناوبات" / "NQ_فوركس" / "سجل أزرار التشغيل.md"

def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "غير مسمّى"
    result = sys.argv[2] if len(sys.argv) > 2 else "START"
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists():
        LOG.write_text("# سجل أزرار التشغيل\n\n| الوقت | الزر | الحالة |\n|---|---|---|\n", encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"| `{now}` | {name.replace('|', '/')} | {result} |\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
