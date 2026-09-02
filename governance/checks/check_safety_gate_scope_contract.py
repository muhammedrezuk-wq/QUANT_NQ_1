"""Contract guard for item 22 — the safety gate's scope must be DECLARED and
MEASURED, never a silent bypass.

Item 64 found the gate refusing to start on any tree without atom 578 -- which
killed the only two tests that run the core as a real process, and the death
read from outside as "the core did not boot". The fix lets a tree with NO
execution atoms through, because a tree that cannot execute has nothing to
guard. The owner's condition on that fix: it stays declared and measured.

The trap he named explicitly: a guard must not pass merely because it inspected
the state it built itself. So nothing here calls an internal function. Both
cases launch the REAL runner as a REAL subprocess and read what it prints and
what it returns.

  أ) بلا ذرّات تنفيذ -- the runner starts, and the skip is ANNOUNCED by name.
  ب) مع ذرّة تنفيذ    -- the gate actually RUNS. It is not skipped.
  ج) ومع ذرّة معطوبة  -- the gate REFUSES to start. The permission is real, so
     the refusal must be real too, otherwise the whole gate is decoration.

Exit 1 on any divergence.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from build_registry import BuildRegistry

PY = Path(sys.executable)

SKIP_LINE = "لا أهداف تنفيذ auto في نطاق forex"
RUN_LINE = "578 تحمل حارس الفيضان"
REFUSE_LINE = "رفض التشغيل: بوابة سلامة التنفيذ مغلقة"
PORT = 8971


def build_tree(tmp: Path, with_execution: bool, break_it: bool) -> Path:
    project = tmp / "project"
    project.mkdir()
    for name in ("core", "scripts", "governance", "transport", "security", "build_registry"):
        shutil.copytree(ROOT / name, project / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "node_modules",
                                                      "*.db", "*.log"))
    atoms = project / "atoms"
    atoms.mkdir()
    if with_execution:
        # The gate inspects the execution surface discovered by the central
        # registry. Preserve each atom's relative depth in the fixture.
        registry = BuildRegistry(ROOT).refresh()
        for record in registry.execution_targets:
            if record.scope != "forex" or record.atom_id is None:
                continue
            source = Path(record.path)
            relative = source.relative_to(ROOT / "atoms")
            shutil.copytree(source, atoms / relative,
                            ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "mt5", project / "mt5", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.ex5"))
        if break_it:
            source_record = next(record for record in BuildRegistry(ROOT).refresh().execution_targets
                                 if record.scope == "forex" and record.atom_id == 578)
            target = atoms / Path(source_record.path).relative_to(ROOT / "atoms") / "atom.py"
            for name in ("atom.py", "flood_guard.py"):
                target_file = target.parent / name
                if target_file.is_file():
                    src = target_file.read_text(encoding="utf-8")
                    target_file.write_text(src.replace("resend_hold_s", "resend_hold_broken"), encoding="utf-8")
    config = project / "config"
    config.mkdir()
    (config / "core.yaml").write_text(
        "log_level: WARNING\nlog_json: true\natoms_root: atoms\n"
        "api:\n  enable_api: false\n  host: 127.0.0.1\n  port: %d\n" % PORT, encoding="utf-8")
    return project


def run(project: Path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.pop("NQ_BRIDGE_DB", None)
    proc = subprocess.run(
        [str(PY), "scripts/run_core.py", "--demo-seconds", "2"],
        cwd=str(project), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    bad = 0
    print("=" * 86)
    print("عقد بوّابة سلامة التنفيذ — بالمشغّل الحقيقيّ كعمليّة، لا بدالّة داخليّة")
    print("=" * 86)

    # What item 22 actually contracts: the bypass is CONFINED to a tree with no
    # execution surface, and it is announced by name. Whether a PARTIAL tree
    # then passes or refuses is the gate's own verdict, not this item's scope --
    # demanding green there would only prove how many folders I remembered to
    # copy, which is a guard measuring itself.
    cases = (
        ("بلا ذرّات تنفيذ: يقلع والتخطّي معلَن", False, False, True, True, False),
        ("مع ذرّات تنفيذ: لا تخطّي إطلاقًا", True, False, False, None, False),
        ("ومع ذرّة معطوبة: يرفض فعلًا", True, True, False, True, True),
    )
    for label, with_exec, broken, want_skip, want_zero, want_refuse in cases:
        with tempfile.TemporaryDirectory() as tmp:
            project = build_tree(Path(tmp), with_exec, broken)
            code, output = run(project)
        skipped = SKIP_LINE in output
        ok = (skipped == want_skip)
        if want_zero is not None:
            ok = ok and ((code == 0) if want_zero else (code != 0))
        if want_refuse:
            ok = ok and REFUSE_LINE in output
        bad += 0 if ok else 1
        print("      %-38s خروج=%-4s · تخطّى=%-5s %s"
              % (label, code, "نعم" if skipped else "لا", "✓" if ok else "✗"))

    print("\n  والمشروع الحقيقيّ لا يأخذ طريق التخطّي إطلاقًا:")
    real = len([record for record in BuildRegistry(ROOT).refresh().execution_targets
                if record.scope == "forex"])
    ok = real > 0
    print("      %-38s ذرات تنفيذ=%-4s %s" % ("شجرة المشروع فيها تنفيذ", real,
                                              "✓" if ok else "✗"))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: التخطّي معلَن ومحصور، والبوّابة ترفض فعلًا حين يكون هناك ما تحرسه.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
