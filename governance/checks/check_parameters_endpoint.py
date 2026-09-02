#!/usr/bin/env python3
"""Check the /gov/parameters endpoint logic by direct function call (no HTTP).

Builds a temporary parameters registry (the live database is never touched),
calls governance.server.parameters_rows directly, and verifies the contract:
every row of the parameters table comes back with its full identity
(name/scope/value/source/status/version/approved_by/approved_at/governs/
declared_at), reading approves nothing, and only the declared analytical
parameters (shared.parameter_registry.DECLARED) are flagged approvable --
decision dials keep their own decision_setting path.
"""
from __future__ import annotations

import gc
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from governance import server
    from shared.decision_dials import DIALS, declare
    from shared.parameter_registry import DECLARED, ParameterRegistry

    problems: list[str] = []

    source = (ROOT / "governance" / "server.py").read_text(encoding="utf-8")
    if '"/gov/parameters"' not in source:
        problems.append("route /gov/parameters missing from governance/server.py")
    if "?mode=ro" not in source:
        problems.append("read-only mode=ro missing from governance/server.py")
    if "parameter_approve" not in server._DANGER_COMMANDS:
        problems.append("parameter_approve missing from the two-step allowlist")

    # ignore_cleanup_errors: registry sqlite handles close on GC, and Windows
    # refuses to delete a file with a live handle -- not an endpoint failure.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "params.db"
        declare(ParameterRegistry(db))  # DECLARED rows + decision dial rows
        result = server.parameters_rows(db)
        if result.get("available") is not True:
            problems.append("temp registry reported unavailable")
        rows = {row["name"]: row for row in result.get("parameters", [])}
        expected = len(DECLARED) + len(DIALS)
        if len(rows) != expected:
            problems.append("expected %d rows, got %d" % (expected, len(rows)))
        required_keys = {"name", "scope", "value", "source", "status", "version",
                         "approved_by", "approved_at", "governs", "declared_at",
                         "approvable"}
        for name, row in rows.items():
            missing = required_keys - set(row)
            if missing:
                problems.append("%s: missing keys %s" % (name, sorted(missing)))
                continue
            if row["status"] != "UNAPPROVED" or row["source"] != "UNSET":
                problems.append("%s: fresh registry must stay UNSET/UNAPPROVED "
                                "after a read" % name)
            if bool(row["approvable"]) != (name in DECLARED):
                problems.append("%s: approvable flag wrong" % name)
        for name in DECLARED:
            if name not in rows:
                problems.append("%s: declared parameter missing from rows" % name)
        absent = server.parameters_rows(Path(tmp) / "absent.db")
        if absent.get("available") is not False or absent.get("parameters"):
            problems.append("missing database must report available=False "
                            "with zero rows, not invented ones")
        gc.collect()  # release lingering sqlite handles before cleanup

    print("check /gov/parameters (direct call, temporary registry only)")
    print("declared=%d dials=%d" % (len(DECLARED), len(DIALS)))
    if problems:
        for problem in problems:
            print("FAIL " + problem)
        return 1
    print("OK endpoint contract holds; the live database was never opened")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
