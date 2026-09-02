"""Small independent node runner used by backup/archive before-after proofs."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def run_cases(cases: list[str], label: str) -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    passed = 0
    failed: list[str] = []
    for case in cases:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", case],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
        )
        if result.returncode == 0:
            passed += 1
            print("PASS", case)
        else:
            failed.append(case)
            print("FAIL", case)
            print(result.stdout)
            print(result.stderr)
    print(f"{label}={passed} PASS {len(failed)} FAIL")
    return 0 if not failed else 1
