"""Compatibility wrapper — `governance` is the canonical path (item 21).

Running this file directly puts `scripts/` on sys.path, not the project root, so
the forward below raised ModuleNotFoundError and the process died before doing
anything. That is exactly how item 64 hid for four days. The bootstrap is the
ONLY logic allowed here: no implementation lives in a wrapper.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from governance.scripts.validate_atoms import main
if __name__ == "__main__":
    raise SystemExit(main())
