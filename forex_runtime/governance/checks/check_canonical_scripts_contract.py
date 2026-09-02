"""Contract guard for item 21 — one canonical run path, and no wrapper that lies.

Owner's ruling: `governance\\scripts` is canonical; the `scripts\\` wrappers are
kept (never deleted) but they must NOT be a second source of truth.

What item 64 proved the hard way: `scripts\\run_core.py` forwarded with
`from governance.scripts.run_core import main`, but running it as
`python scripts/run_core.py` puts `scripts/` on sys.path -- not the project root
-- so the import raised ModuleNotFoundError and the process died before opening
a port. From the outside that read as "the core did not boot", and it killed the
only two tests that run the core as a real process for four days.

Every other wrapper in `scripts\\` is four lines with the SAME shape and the
SAME defect: they are forwarders that cannot forward.

  أ) لا منطق مكرَّر -- a wrapper carries no implementation: it bootstraps the
     path, imports the canonical module, and calls it. Nothing else.
  ب) وتُشغَّل فعلًا -- each wrapper is RUN as a real subprocess. A forwarder that
     dies on import is not a forwarder; it is a trap. This is the barrier the
     four-line shape cannot pass.

Exit 1 on any divergence.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WRAPPERS = ROOT / "scripts"
CANONICAL = ROOT / "governance"
MAX_WRAPPER_LINES = 24
PY = ROOT / "venv" / "Scripts" / "python.exe"
IMPORT_ERROR = "ModuleNotFoundError"


def wrappers() -> list:
    return [p for p in sorted(WRAPPERS.glob("*.py")) if p.name != "__init__.py"]


def structural() -> int:
    print("=" * 86)
    print("أ) الأغلفة لا تحمل منطقًا — المسار الأصل هو governance")
    print("=" * 86)
    bad = 0
    for path in wrappers():
        src = path.read_text(encoding="utf-8")
        lines = len(src.splitlines())
        forwards = re.search(r"^from\s+governance[.\s]", src, re.M) is not None
        defs = len(re.findall(r"^\s*(def|class)\s+", src, re.M))
        ok = forwards and lines <= MAX_WRAPPER_LINES and defs == 0
        bad += 0 if ok else 1
        print("      %-30s %3d سطر · يحوّل=%-4s · تعريفات=%d %s"
              % (path.name, lines, "نعم" if forwards else "لا", defs,
                 "✓" if ok else "✗"))
    return bad


def behavioural() -> int:
    print("\n" + "=" * 86)
    print("ب) وكلّ غلاف يُشغَّل فعلًا — لا استيراد ينهار")
    print("=" * 86)
    bad = 0
    # The 601/609 lesson again: a polluted environment lies to the check. With
    # PYTHONPATH pointing at the project the wrappers import fine and the defect
    # is invisible -- but the owner's dashboard and a plain double-click do not
    # set it. The child is run with PYTHONPATH REMOVED, which is the real case.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    for path in wrappers():
        proc = subprocess.run([str(PY), str(path), "--help"], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=90, env=env)
        broken = IMPORT_ERROR in (proc.stderr or "")
        bad += 1 if broken else 0
        print("      %-30s %s" % (path.name,
                                  "✗ %s" % IMPORT_ERROR if broken else "✓ يستورد ويعمل"))
    return bad


def main() -> int:
    bad = structural() + behavioural()
    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: مسار أصل واحد · والأغلفة تحوّل فعلًا ولا تكون مصدر حقيقة ثانيًا.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
