"""Run the fast QUANT_NQ smoke suite without starting the trading runtime."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    pytest = shutil.which("pytest") or sys.executable
    command = [pytest, "-q", "tests/smoke"] if pytest != sys.executable else [sys.executable, "-m", "pytest", "-q", "tests/smoke"]
    print("QUANT_NQ FAST SMOKE")
    print("scope: config + launchers + API exposure + secret contract")
    print("runtime: NOT STARTED")
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
