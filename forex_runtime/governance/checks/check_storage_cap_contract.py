"""Contract guard: every storage atom must carry a REAL byte ceiling.

Owner's finding, 2026-08-18, verbatim:

    "السبب البنيويّ: max_db_bytes: 0 — بلا سقف — في ٧١٢ و٧١٣ و٧١٨، بينما جارها
     701 يفرض سقف ٢ جيجا. وأوّل آلية تنظيف (archive_after_days: 60) تستيقظ بعد
     ٦٠ يومًا — أي بعد استنفاد القرص بـ٥٫٤ أضعاف."

Measured on his machine the same day, which is why a row cap is NOT a substitute:

    analysis.db   371,813 rows in 0.84 days = 11.87 GiB/day, 28.0 KB per row
    structure.db / liquidity.db                              1.9 KB per row
    market_data.db                                           0.6 KB per row

    => `max_rows: 1000000` on 712 first bites at 1,000,000 x 28 KB = 28 GiB.
       The disk dies first. `retention_days: 90` needs 89 more days; atom 714's
       `archive_after_days: 60` needs 60. Nothing arrives in time. Only a byte
       ceiling bounds a store whose row size is not known in advance.

The audit that produced this guard found the defect wider than reported: FIVE of
the ten stores, in two shapes -- 712/713/718 declare `max_db_bytes: 0`, while
702/709 omit the key entirely. Both mean unbounded; only one of them looks wrong.

  A) STRUCTURAL -- every atom that calls `enforce_limits` declares a positive
     `max_db_bytes`, and declares it in `config_schema` so it cannot be dropped
     silently later.
  B) BEHAVIOURAL -- on a real sqlite file: a positive ceiling actually shrinks
     the file, and `0` (or a missing key) actually does NOT. The second half is
     the one that matters: it proves what the defect really costs, so nobody can
     "fix" this by writing a zero back.

Exit 1 on any divergence.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from storage_policy import database_bytes, enforce_limits  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)


def in_scope() -> list[Path]:
    """Scope is derived from the CODE, never from a hand-written list."""
    return sorted(d for d in ATOMS.iterdir()
                  if d.is_dir() and (d / "atom.py").is_file()
                  and "enforce_limits" in (d / "atom.py").read_text("utf-8", errors="ignore"))


def structural() -> int:
    print("=" * 78)
    print("أ) الحاجز البنيويّ — كل مخزن يعلن سقف بايت موجبًا")
    print("=" * 78)
    bad = 0
    folders = in_scope()
    for d in folders:
        manifest = yaml.safe_load((d / "manifest.yaml").read_text("utf-8"))
        cfg = manifest.get("config") or {}
        schema = manifest.get("config_schema") or {}
        required = schema.get("required") or []
        cap = cfg.get("max_db_bytes")
        has = isinstance(cap, int) and cap > 0
        props = schema.get("properties") or {}
        spec = props.get("max_db_bytes")
        declared = isinstance(spec, dict)
        pinned = "max_db_bytes" in required
        # The hole that made this possible: the schema itself allowed `0`, so a
        # store with no ceiling was a *valid* store. 704/706/707 already carry
        # `minimum: 1`; the guard makes that the rule for every one of them, so
        # a zero can never be written back and pass validation.
        floored = declared and int(spec.get("minimum", 0)) >= 1
        ok = has and declared and pinned and floored
        bad += 0 if ok else 1
        shown = ("مفقود" if cap is None else
                 "0 — بلا سقف" if cap == 0 else "%.2f جيجا" % (cap / 1073741824.0))
        print("  %-6s %-20s %-15s عقد=%s مطلوب=%s أدنى≥1=%s  %s" % (
            d.name.split("_")[0], d.name.split("_", 1)[1][:20], shown,
            "✓" if declared else "✗", "✓" if pinned else "✗",
            "✓" if floored else "✗", "✓" if ok else "✗"))
    print("  المخازن المفحوصة: %d" % len(folders))
    return bad


def _grow(path: Path, rows: int, blob: int) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE store (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT)")
        payload = "x" * blob
        con.executemany("INSERT INTO store (body) VALUES (?)",
                        [(payload,) for _ in range(rows)])
        con.commit()
    finally:
        con.close()


def behavioural() -> int:
    print()
    print("-" * 78)
    print("ب) الحاجز السلوكيّ — على قاعدة حقيقيّة: السقف الموجب يقصّ، والصفر لا")
    print("-" * 78)
    bad = 0
    tmp = Path(tempfile.mkdtemp(prefix="storage_cap_guard_"))
    try:
        seed = tmp / "seed.db"
        _grow(seed, rows=4000, blob=4096)          # ~17 ميجابايت
        start = database_bytes(str(seed))
        cap = start // 4                            # سقف = ربع الحجم

        capped = tmp / "capped.db"
        shutil.copyfile(seed, capped)
        result = enforce_limits(str(capped), "store", max_rows=0, max_db_bytes=cap)
        after_cap = database_bytes(str(capped))
        shrank = after_cap <= cap
        pruned = (result.get("pruned") or 0) > 0
        bad += 0 if (shrank and pruned) else 1
        print("  سقف موجب : %.2f ميجا ← %.2f ميجا (السقف %.2f) · حُذف %s صفّ  %s" % (
            start / 1048576.0, after_cap / 1048576.0, cap / 1048576.0,
            "{:,}".format(result.get("pruned") or 0),
            "✓ قصّ" if (shrank and pruned) else "✗ ما قصّ"))

        loose = tmp / "loose.db"
        shutil.copyfile(seed, loose)
        result0 = enforce_limits(str(loose), "store", max_rows=0, max_db_bytes=0)
        after_zero = database_bytes(str(loose))
        untouched = after_zero >= start and (result0.get("pruned") or 0) == 0
        bad += 0 if untouched else 1
        print("  سقف = 0   : %.2f ميجا ← %.2f ميجا · حُذف %s صفّ  %s" % (
            start / 1048576.0, after_zero / 1048576.0,
            "{:,}".format(result0.get("pruned") or 0),
            "✓ لم يُقصّ إطلاقًا — الصفر يعني بلا حدّ" if untouched else "✗"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return bad


def main() -> int:
    bad = structural() + behavioural()
    print()
    print("=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: كل مخزن يحمل سقف بايت موجبًا مثبّتًا بعقده، والسقف يقصّ فعلًا.")
    else:
        print("الأثر: مخزن بلا سقف بايت ينمو حتى يمتلئ القرص — ولا آليّة تنظيف")
        print("       أخرى تصل في الوقت (المقيس 2026-08-18: 11.87 جيجا/يوم).")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
