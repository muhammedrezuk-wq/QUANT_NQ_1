"""Contract guard: the owner's snapshot button must actually produce a snapshot.

Owner's report, 2026-08-18, from the dashboard:

    "💾 النسخة الاحتياطية الموحّدة ... خُد لقطة
     🛑 [Errno 9] Bad file descriptor"
    "اليدوية (لقطات النظام): لا نسخ بعد"

Root cause, measured the same day on his machine:

    open(path, "rb") + os.fsync(fd)  ->  OSError [Errno 9] Bad file descriptor
    open(path, "r+b") + os.fsync(fd) ->  OK
    open(path, "ab")  + os.fsync(fd) ->  OK

`os.fsync` on Windows maps to `_commit()`, which requires a handle opened for
WRITING. A read-only handle is rejected. On Linux the same call is accepted, so
the defect is invisible off-Windows -- the exact class the owner's standing rule
targets: fix the Windows-incompatible code, never route around it.

What it cost: every manual snapshot died AFTER the archive was fully written and
verified, at the very last durability step, and the `except` branch then DELETED
the good archive. The owner had zero restore points and did not know why.

  A) STRUCTURAL -- no `os.fsync` anywhere in the project is reachable from a
     read-only handle, and the snapshot's own durability step opens for writing.
  B) BEHAVIOURAL -- the button's real function is called for real: it must
     return ok, write the file, and the archive on disk must open, pass
     `testzip()`, and carry every file in `_BACKUP_MUST_CONTAIN` plus the
     restore guide. A snapshot that cannot be reopened is not a restore point.

SIDE EFFECT, deliberate and stated: (B) calls the production `make_backup()`,
so every run leaves one real snapshot (~3.5 MB) in `var/backups`. Mocking it
would guard nothing -- the defect lived in the real call. Growth is bounded by
the function's own retention (`_SNAPSHOT_KEEP = 10`).

Exit 1 on any divergence.
"""
from __future__ import annotations

import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# `open(...)` modes that CANNOT be fsynced on Windows: no write intent at all.
_READ_ONLY_MODE = re.compile(r"""open\(\s*[^)]*?["'](r|rb|br)["']\s*\)""")


def _fsync_sites() -> list[tuple[Path, int, str]]:
    """Every `os.fsync` in the project, with the `open(...)` that feeds it."""
    found: list[tuple[Path, int, str]] = []
    skip = {"venv", "var", "node_modules", "__pycache__", ".git",
            ".pytest_cache", ".ruff_cache", "backups"}
    me = Path(__file__).resolve()
    for path in ROOT.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        # هذا الملفّ نفسه يقتبس العطل حرفيًّا في وثيقته ليشرحه — فلا يُحاكَم به.
        if path.resolve() == me:
            continue
        lines = path.read_text("utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if "os.fsync" not in line or line.lstrip().startswith("#"):
                continue
            # the open() may sit on this line (`with p.open("rb") as h: fsync`)
            # or on the line above it.
            window = "\n".join(lines[max(0, i - 1):i + 1])
            found.append((path, i + 1, window))
    return found


def structural() -> int:
    print("=" * 78)
    print("أ) الحاجز البنيويّ — لا fsync على مقبض قراءة-فقط")
    print("=" * 78)
    bad = 0
    sites = _fsync_sites()
    for path, line, window in sites:
        readonly = bool(_READ_ONLY_MODE.search(window))
        bad += 1 if readonly else 0
        print("  %-46s سطر %-5s %s" % (
            path.relative_to(ROOT).as_posix()[:46], line,
            "✗ قراءة-فقط — يسقط على ويندوز" if readonly else "✓ مفتوح للكتابة"))
    if not sites:
        print("  (لا وجود لـ os.fsync إطلاقًا)")
    print("  المواضع المفحوصة: %d" % len(sites))
    return bad


def behavioural() -> int:
    print()
    print("-" * 78)
    print("ب) الحاجز السلوكيّ — الزرّ يُستدعى فعلًا ويُقرأ ناتجه من القرص")
    print("-" * 78)
    bad = 0
    from governance import server

    before = set(server.BACKUPS_DIR.glob("snapshot_*.zip")) \
        if server.BACKUPS_DIR.is_dir() else set()
    status, body = server.make_backup()
    ok = status == 200 and body.get("ok") is True
    bad += 0 if ok else 1
    print("  استدعاء make_backup()  → %s  %s" % (
        status, "✓" if ok else "✗ " + str(body.get("message"))[:60]))
    if not ok:
        return bad

    created = set(server.BACKUPS_DIR.glob("snapshot_*.zip")) - before
    made = len(created) == 1
    bad += 0 if made else 1
    print("  ملفّ جديد على القرص     → %d  %s" % (len(created), "✓" if made else "✗"))
    if not made:
        return bad

    snap = created.pop()
    with zipfile.ZipFile(snap) as z:
        inside = {n.replace("\\", "/") for n in z.namelist()}
        corrupt = z.testzip()
    missing = [m for m in server._BACKUP_MUST_CONTAIN if m not in inside]
    guide = "اقرأني — كيف أرجّع المشروع من هالملف.md" in inside
    for label, good in (("الأرشيف يُفتح ويمرّ testzip", corrupt is None),
                        ("كل الملفّات الجوهريّة موجودة", not missing),
                        ("وصفة الاسترجاع جوّاته", guide)):
        bad += 0 if good else 1
        print("  %-34s %s" % (label, "✓" if good else "✗"))
    if missing:
        print("      الناقص: %s" % " · ".join(missing))
    print("  المحتوى: %s ملف · %.1f ميجا" % (
        "{:,}".format(len(inside)), snap.stat().st_size / 1048576.0))
    return bad


def main() -> int:
    bad = structural() + behavioural()
    print()
    print("=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: زرّ اللقطة ينتج نقطة رجوع حقيقيّة تُفتح وتُقرأ ولا ينقصها شيء.")
    else:
        print("الأثر: المالك يضغط الزرّ ولا يحصل على نقطة رجوع — والأرشيف السليم")
        print("       يُحذف في مسار الخطأ، فلا يبقى أثر يدلّه على السبب.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
