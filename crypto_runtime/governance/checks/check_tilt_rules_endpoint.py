#!/usr/bin/env python3
"""Check the /gov/tilt/rules endpoint logic by direct function call (no HTTP).

Builds a temporary tilt rules store (the live database is never touched),
calls governance.server.tilt_rules_rows directly, and verifies the contract
(package TH item TH3, paper Q10 S18-S21): every stored rule comes back with
its full identity (field/side/points/enabled/version/updated_at/updated_by),
reading writes nothing, a missing file or unreadable database reports
available=False with zero invented rules, corrupt points_json comes back as
points=None (never an invented empty curve), and the two-step allowlist
carries the tilt_rule action with its payload validator.
"""
from __future__ import annotations

import gc
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCHEMA = (
    "CREATE TABLE tilt_rules ("
    "field TEXT NOT NULL, side TEXT NOT NULL, points_json TEXT NOT NULL, "
    "enabled INTEGER NOT NULL, version INTEGER NOT NULL, "
    "updated_at REAL NOT NULL, updated_by TEXT NOT NULL, "
    "PRIMARY KEY (field, side))")


def main() -> int:
    from governance import server

    problems: list[str] = []

    source = (ROOT / "governance" / "server.py").read_text(encoding="utf-8")
    if '"/gov/tilt/rules"' not in source:
        problems.append("route /gov/tilt/rules missing from governance/server.py")
    if "?mode=ro" not in source:
        problems.append("read-only mode=ro missing from governance/server.py")
    if "tilt_rule" not in server._DANGER_COMMANDS:
        problems.append("tilt_rule missing from the two-step allowlist")

    # The server-side payload validator must mirror gate 901: ascending
    # thresholds, finite numbers only, twelve points at most, empty legal.
    valid = server._tilt_points_valid
    if not valid([[80, 0.1], [85, 0.2]]) or not valid([]):
        problems.append("validator rejects a legal curve")
    for bad in ([[85, 0.2], [80, 0.1]],            # descending
                [[80, 0.1], [80, 0.2]],            # duplicate threshold
                [[80, "abc"]],                      # non-numeric amount
                [[80, True]],                       # boolean is not a number
                [[float("nan"), 0.1]],              # non-finite threshold
                [[i, 0.01] for i in range(13)],     # beyond twelve points
                "not-a-list", [[80]], [[80, 0.1, 5]]):
        if valid(bad):
            problems.append("validator accepts invalid points: %r" % (bad,))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "tilt_rules.db"
        conn = sqlite3.connect(db)
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO tilt_rules VALUES (?,?,?,?,?,?,?)",
            ("confidence", "up", json.dumps([[80.0, 0.1], [85.0, 0.2]]),
             1, 3, 1000.0, "dashboard"))
        conn.execute(
            "INSERT INTO tilt_rules VALUES (?,?,?,?,?,?,?)",
            ("direction", "abs", "{corrupt", 0, 1, 900.0, "dashboard"))
        conn.commit()
        conn.close()

        result = server.tilt_rules_rows(db)
        if result.get("available") is not True:
            problems.append("temp store reported unavailable")
        rules = {(r["field"], r["side"]): r for r in result.get("rules", [])}
        if len(rules) != 2:
            problems.append("expected 2 rules, got %d" % len(rules))
        good = rules.get(("confidence", "up"))
        if good is None:
            problems.append("confidence/up rule missing from rows")
        else:
            required_keys = {"field", "side", "points", "enabled", "version",
                             "updated_at", "updated_by"}
            missing = required_keys - set(good)
            if missing:
                problems.append("rule missing keys %s" % sorted(missing))
            if good.get("points") != [[80.0, 0.1], [85.0, 0.2]]:
                problems.append("points came back altered: %r" % (good.get("points"),))
            if good.get("enabled") is not True or good.get("version") != 3:
                problems.append("enabled/version identity lost")
            if good.get("updated_by") != "dashboard" or good.get("updated_at") != 1000.0:
                problems.append("updated_by/updated_at identity lost")
        corrupt = rules.get(("direction", "abs"))
        if corrupt is None:
            problems.append("corrupt-points rule missing from rows")
        elif corrupt.get("points") is not None:
            problems.append("corrupt points_json must come back as points=None, "
                            "not an invented curve: %r" % (corrupt.get("points"),))
        elif corrupt.get("enabled") is not False:
            problems.append("disabled rule must come back enabled=False")

        absent = server.tilt_rules_rows(Path(tmp) / "absent.db")
        if absent.get("available") is not False or absent.get("rules"):
            problems.append("missing database must report available=False "
                            "with zero rules, not invented ones")

        broken = Path(tmp) / "broken.db"
        broken.write_text("this is not a sqlite database", encoding="utf-8")
        unreadable = server.tilt_rules_rows(broken)
        if unreadable.get("available") is not False or unreadable.get("rules"):
            problems.append("unreadable database must report available=False "
                            "with zero rules")
        gc.collect()  # release lingering sqlite handles before cleanup

    print("check /gov/tilt/rules (direct call, temporary store only)")
    if problems:
        for problem in problems:
            print("FAIL " + problem)
        return 1
    print("OK endpoint contract holds; the live store was never opened")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
