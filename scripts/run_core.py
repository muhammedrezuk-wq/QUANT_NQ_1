"""Compatibility wrapper — the dashboard remains the normal control surface.

Item 29/64: `governance\\scripts` is the canonical path; this file only forwards.
But running it as `python scripts/run_core.py` puts `scripts/` on sys.path, NOT
the project root -- so the forward raised ModuleNotFoundError and the core died
before it opened a port. Anyone watching read that as "the core did not boot",
and the only two tests that run the core as a REAL process were dead from
2026-08-11 until this line was added.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Keep the project root ahead of scripts/ so the build_registry package is not
# shadowed by scripts/build_registry.py when this wrapper is executed directly.
if str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from governance.scripts.run_core import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
