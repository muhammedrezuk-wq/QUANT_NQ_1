from __future__ import annotations

import json
import sqlite3
from typing import Any

from ruling_math import RESTORE_JOURNAL, ID_ENGINE, _valid_points, EIGHT_FIELDS

# Campaign 450-901 batch B: rules loading and journal management extracted
# verbatim from the original atom -- same behavior, smaller atom.

def _load_rules(atom) -> None:
    try:
        connection = atom._connect()
        try:
            rows = connection.execute(
                "SELECT field, side, points_json, enabled, version,"
                " updated_at, updated_by FROM tilt_rules"
                " ORDER BY field, side").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        atom._db_error = "RULES_LOAD_FAILED:%s" % exc
        return
    for row in rows:
        try:
            decoded = json.loads(str(row["points_json"]))
        except ValueError:
            decoded = None
        points = _valid_points(decoded) if isinstance(decoded, list) else None
        if points is None:
            # Corrupt points come back as None -- never an invented curve
            # (same law as the governance read endpoint).
            atom._corrupt_rules += 1
        atom._rules[(str(row["field"]), str(row["side"]))] = {
            "field": str(row["field"]), "side": str(row["side"]),
            "points": points, "enabled": bool(row["enabled"]),
            "version": int(row["version"]),
            "updated_at": float(row["updated_at"]),
            "updated_by": str(row["updated_by"])}
    atom._restored_rules = len(rows)

def _load_journal_tail(atom) -> None:
    """S39: the last recorded tilt per symbol, rebuilt from the journal.
    Only what the journal really holds is restored -- fields absent from
    it stay absent, declared through restore_source."""
    try:
        connection = atom._connect()
        try:
            symbols = [str(row["symbol"]) for row in connection.execute(
                "SELECT DISTINCT symbol FROM tilt_state_journal")]
            for symbol in symbols:
                head = connection.execute(
                    "SELECT rowid, decision_id, changed_at, total"
                    " FROM tilt_state_journal WHERE symbol=?"
                    " ORDER BY rowid DESC LIMIT 1", (symbol,)).fetchone()
                if head is None:
                    continue
                rows = connection.execute(
                    "SELECT field, value, tilt FROM tilt_state_journal"
                    " WHERE symbol=? AND decision_id=? AND changed_at=?"
                    " ORDER BY rowid", (symbol, head["decision_id"],
                                        head["changed_at"])).fetchall()
                contributions = {
                    str(row["field"]): {"value": row["value"],
                                        "tilt": float(row["tilt"])}
                    for row in rows}
                atom._last_tilt[symbol] = {
                    "id": ID_ENGINE, "symbol": symbol,
                    "decision_id": str(head["decision_id"]),
                    "contributions": contributions,
                    "total_capped": float(head["total"]),
                    "tilt_max_total": atom._tilt_max_total,
                    "source_timestamp": float(head["changed_at"]),
                    "restored": True, "restore_source": RESTORE_JOURNAL}
        finally:
            connection.close()
    except sqlite3.Error as exc:
        atom._db_error = "JOURNAL_LOAD_FAILED:%s" % exc
        return
    atom._restored_symbols = len(atom._last_tilt)

def _journal(atom, symbol: str, decision_id: str,
             contributions: dict[str, dict[str, Any]], total: float,
             changed_at: float) -> None:
    try:
        connection = atom._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for field in EIGHT_FIELDS:
                entry = contributions.get(field) or {}
                connection.execute(
                    "INSERT INTO tilt_state_journal"
                    "(symbol, decision_id, field, value, tilt, total,"
                    " changed_at) VALUES(?,?,?,?,?,?,?)",
                    (symbol, decision_id, field, entry.get("value"),
                     float(entry.get("tilt") or 0.0), total, changed_at))
            connection.execute("COMMIT")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        atom._db_error = "JOURNAL_WRITE_FAILED:%s" % exc
        return
    atom._db_error = ""
    atom._journal_writes += 1
