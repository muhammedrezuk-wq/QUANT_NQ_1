"""Print the central Build Registry snapshot without changing the project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_registry import BuildRegistry, evaluate_release  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only QUANT_NQ Build Registry")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--components", action="store_true")
    parser.add_argument("--strict", action="store_true", help="return non-zero for unresolved build conflicts")
    args = parser.parse_args()
    snapshot = BuildRegistry(args.root).refresh()
    data = snapshot.as_dict(include_components=args.components)
    data["release_gate"] = evaluate_release(snapshot).as_dict()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if args.strict and snapshot.build_contract_status != "RESOLVED":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
