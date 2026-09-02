"""Reject regression to symbol-only financial state in the scoped money path."""
from __future__ import annotations

import sys
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

TARGETS={"508","513","560","563","525","551","552","573","576","578","579","583"}
FINANCIAL_NAMES={"_specs","_vpu","_price","_sizes","_spread","_stops","_divergence","_pending_by_ticket","_points"}


def main()->int:
    failures=[]
    for folder in (ATOM_ROOT).iterdir():
        if folder.name[:3] not in TARGETS:continue
        path=folder/"atom.py";tree=ast.parse(path.read_text(encoding="utf-8"),filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node,ast.Subscript):continue
            value=node.value
            if not (isinstance(value,ast.Attribute) and value.attr in FINANCIAL_NAMES):continue
            expr=ast.unparse(node.slice)
            if expr in {"symbol","str(symbol)","payload.get('symbol')","row['symbol']","str(row['symbol'])","ticket","str(ticket)"}:
                failures.append(f"{folder.name}:{node.lineno}: {value.attr}[{expr}]")
    if failures:
        print("FINANCIAL_SCOPE_CONTRACT=FAIL")
        for failure in failures:print(failure)
        return 1
    print("FINANCIAL_SCOPE_CONTRACT=PASS (account + broker + symbol; execution account + request)")
    return 0
if __name__=="__main__":raise SystemExit(main())
