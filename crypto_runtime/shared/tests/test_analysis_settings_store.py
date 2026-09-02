"""Self-tests for the analysis settings store (owner stamp NQ-22: Q1/Q2/Q4).

Run directly, no pytest:  venv\\Scripts\\python.exe shared\\tests\\test_analysis_settings_store.py
Every database here is a temporary file -- the live store is never touched.
"""
import contextlib
import gc
import sqlite3
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.live_analysis import (  # noqa: E402
    AnalysisSettingsStore,
    DEFAULT_WEIGHTS,
    SECTION_DEFAULT_WEIGHTS,
    SECTION_IDS,
)

# The production schema exactly as it was BEFORE the path dimension existed.
OLD_SCHEMA = """
    CREATE TABLE analysis_settings (
        account_id TEXT NOT NULL,
        broker TEXT NOT NULL,
        symbol TEXT NOT NULL,
        analyzer_id TEXT NOT NULL,
        required_depth REAL NOT NULL CHECK(required_depth BETWEEN 0 AND 100),
        confidence_threshold REAL NOT NULL CHECK(confidence_threshold BETWEEN 0 AND 100),
        weight REAL NOT NULL CHECK(weight BETWEEN 0 AND 100),
        revision INTEGER NOT NULL,
        updated_at REAL NOT NULL,
        updated_by TEXT NOT NULL,
        PRIMARY KEY(account_id, broker, symbol, analyzer_id)
    );
    CREATE TABLE analysis_settings_audit (
        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        broker TEXT NOT NULL,
        symbol TEXT NOT NULL,
        analyzer_id TEXT NOT NULL,
        old_json TEXT NOT NULL,
        new_json TEXT NOT NULL,
        revision INTEGER NOT NULL,
        changed_at REAL NOT NULL,
        changed_by TEXT NOT NULL,
        command_id TEXT NOT NULL
    );
    CREATE UNIQUE INDEX uq_analysis_settings_command
    ON analysis_settings_audit(command_id, account_id, broker, symbol, analyzer_id);
"""

SCOPE = ("A1", "BR", "NQ100")


@contextlib.contextmanager
def temp_db():
    """Temporary database path. The store's short-lived sqlite connections
    are reclaimed only by the cyclic GC, so collect before the directory is
    removed -- otherwise Windows refuses to unlink the still-open file."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        try:
            yield Path(tmp) / "analysis_settings.db"
        finally:
            gc.collect()


def run_sql(db, script=None, statement=None, params=()):
    connection = sqlite3.connect(db)
    try:
        if script is not None:
            connection.executescript(script)
        if statement is not None:
            connection.execute(statement, params)
        connection.commit()
    finally:
        connection.close()


def fetch(db, sql, params=()):
    connection = sqlite3.connect(db)
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()


def all_weights(store, path="fast"):
    return {aid: store.get(*SCOPE, aid, path)["weight"] for aid in DEFAULT_WEIGHTS}


def weight_sum(store, path="fast"):
    return round(sum(all_weights(store, path).values()), 4)


def test_path_column_migration_on_old_database():
    with temp_db() as db:
        run_sql(db, script=OLD_SCHEMA)
        run_sql(db, statement="""INSERT INTO analysis_settings
            VALUES('A1','BR','NQ100','trend',70.0,65.0,23.0,3,111.0,'owner')""")
        run_sql(db, statement="""INSERT INTO analysis_settings_audit(account_id,
            broker,symbol,analyzer_id,old_json,new_json,revision,changed_at,
            changed_by,command_id)
            VALUES('A1','BR','NQ100','trend','{}','{"revision": 3}',3,111.0,
            'owner','legacy-1')""")
        store = AnalysisSettingsStore(db)
        settings_cols = [r["name"] for r in fetch(db, "PRAGMA table_info(analysis_settings)")]
        audit_cols = [r["name"] for r in fetch(db, "PRAGMA table_info(analysis_settings_audit)")]
        assert "path" in settings_cols, settings_cols
        assert "path" in audit_cols, audit_cols
        assert [r["path"] for r in fetch(db, "SELECT path FROM analysis_settings")] == ["fast"]
        assert [r["path"] for r in fetch(db, "SELECT path FROM analysis_settings_audit")] == ["fast"]
        migrated = store.get("A1", "BR", "NQ100", "trend")
        assert (migrated["required_depth"], migrated["weight"], migrated["revision"]) == (70.0, 23.0, 3)
        # The grown unique key lets a slow row live beside the fast row.
        store.update("A1", "BR", "NQ100", "trend", {"required_depth": 50},
                     changed_by="t", command_id="m-1", changed_at=1.0, path="slow")
        assert store.get("A1", "BR", "NQ100", "trend", "slow")["required_depth"] == 50.0
        assert store.get("A1", "BR", "NQ100", "trend")["required_depth"] == 70.0
        both = fetch(db, "SELECT COUNT(*) AS n FROM analysis_settings WHERE analyzer_id='trend'")
        assert both[0]["n"] == 2, both
        # Reopening migrates nothing twice and loses nothing.
        assert AnalysisSettingsStore(db).get("A1", "BR", "NQ100", "trend")["weight"] == 23.0
    print("OK - old rows became the fast camp; slow coexists on the grown key")


def test_owner_example_40_20_20_20():
    with temp_db() as db:
        store = AnalysisSettingsStore(db)
        quarter = ("trend", "momentum", "volatility", "volume")
        for aid in DEFAULT_WEIGHTS:
            run_sql(store.path, statement="""INSERT INTO analysis_settings(
                account_id,broker,symbol,analyzer_id,path,required_depth,
                confidence_threshold,weight,revision,updated_at,updated_by)
                VALUES('A1','BR','NQ100',?,'fast',60.0,60.0,?,1,1.0,'seed')""",
                params=(aid, 25.0 if aid in quarter else 0.0))
        store.update(*SCOPE, "trend", {"weight": 40}, changed_by="owner",
                     command_id="q2-owner", changed_at=2.0)
        weights = all_weights(store)
        assert weights["trend"] == 40.0, weights["trend"]
        for aid in ("momentum", "volatility", "volume"):
            assert round(weights[aid], 4) == 20.0, (aid, weights[aid])
        for aid in DEFAULT_WEIGHTS:
            if aid not in quarter:
                assert weights[aid] == 0.0, (aid, weights[aid])
        assert weight_sum(store) == 100.0, weight_sum(store)
        audit = fetch(store.path, """SELECT command_id, analyzer_id
            FROM analysis_settings_audit ORDER BY audit_id""")
        primary = [r for r in audit if ":eq:" not in r["command_id"]]
        equalized = [r for r in audit if ":eq:" in r["command_id"]]
        assert [r["command_id"] for r in primary] == ["q2-owner"], primary
        # Zero-weight peers were untouched: only the three payers are audited.
        assert sorted(r["analyzer_id"] for r in equalized) == \
            ["momentum", "volatility", "volume"], equalized
        assert all(r["command_id"] == "q2-owner:eq:" + r["analyzer_id"]
                   for r in equalized), equalized
    print("OK - owner example: 25/25/25/25 -> 40/20/20/20, equal charge, audited")


def test_sum_stays_100_after_any_edit():
    with temp_db() as db:
        store = AnalysisSettingsStore(db)
        # First weight edit on a fresh scope: the target's old value is the
        # equal default and the missing peers materialize from it (100/15).
        store.update(*SCOPE, "trend", {"weight": 40}, changed_by="owner",
                     command_id="s-1", changed_at=1.0)
        assert weight_sum(store) == 100.0, weight_sum(store)
        expected_peer = round((100.0 - 40.0) / 14.0, 4)
        for aid in DEFAULT_WEIGHTS:
            if aid != "trend":
                got = round(store.get(*SCOPE, aid)["weight"], 4)
                assert got == expected_peer, (aid, got, expected_peer)
        # Successive edits, including a clamp-heavy raise, keep the camp at 100.
        for step, (aid, value) in enumerate([("momentum", 3.0), ("noise", 0.0),
                                             ("volatility", 77.7), ("gap", 12.34)]):
            store.update(*SCOPE, aid, {"weight": value}, changed_by="owner",
                         command_id="s-%d" % (step + 2), changed_at=float(step + 2))
            assert weight_sum(store) == 100.0, (aid, weight_sum(store))
            assert store.get(*SCOPE, aid)["weight"] == value, aid
        # A depth/threshold edit moves no weight at all.
        before = all_weights(store)
        store.update(*SCOPE, "session", {"required_depth": 80},
                     changed_by="owner", command_id="s-9", changed_at=9.0)
        assert all_weights(store) == before
        # A replayed command id changes nothing a second time.
        snapshot = all_weights(store)
        _, changed = store.update(*SCOPE, "gap", {"weight": 12.34},
                                  changed_by="owner", command_id="s-5",
                                  changed_at=99.0)
        assert changed is False
        assert all_weights(store) == snapshot
    print("OK - camp total is exactly 100.0000 after every edit; replay is inert")


def test_paths_are_independent():
    with temp_db() as db:
        store = AnalysisSettingsStore(db)
        store.update(*SCOPE, "trend", {"weight": 40}, changed_by="owner",
                     command_id="f-1", changed_at=1.0, path="fast")
        fast_before = all_weights(store, "fast")
        store.update(*SCOPE, "trend", {"weight": 10}, changed_by="owner",
                     command_id="w-1", changed_at=2.0, path="slow")
        # The slow edit did not move a single fast value, and both camps sum to 100.
        assert all_weights(store, "fast") == fast_before
        assert store.get(*SCOPE, "trend", "slow")["weight"] == 10.0
        assert store.get(*SCOPE, "trend", "fast")["weight"] == 40.0
        assert weight_sum(store, "slow") == 100.0
        assert weight_sum(store, "fast") == 100.0
        # The same command id on the OTHER path is not swallowed as a duplicate.
        _, changed = store.update(*SCOPE, "trend", {"weight": 40},
                                  changed_by="owner", command_id="f-1",
                                  changed_at=3.0, path="slow")
        assert changed is True
        assert store.get(*SCOPE, "trend", "slow")["weight"] == 40.0
        assert store.get(*SCOPE, "trend", "fast")["weight"] == 40.0
        assert weight_sum(store, "slow") == 100.0
        # Only the two camp names exist.
        try:
            store.update(*SCOPE, "trend", {"weight": 5}, changed_by="o",
                         command_id="x-1", changed_at=4.0, path="medium")
            raise AssertionError("path 'medium' must be refused")
        except ValueError as exc:
            assert str(exc) == "INVALID_ANALYSIS_PATH", exc
    print("OK - fast and slow camps are fully independent")


def test_section_defaults_are_equal_sixth():
    assert set(SECTION_DEFAULT_WEIGHTS) == set(SECTION_IDS)
    assert set(SECTION_DEFAULT_WEIGHTS) == {"150", "200", "250", "300", "350", "400"}
    for sid, value in SECTION_DEFAULT_WEIGHTS.items():
        assert value == 100.0 / 6.0, (sid, value)
    assert round(sum(SECTION_DEFAULT_WEIGHTS.values()), 4) == 100.0
    assert AnalysisSettingsStore.defaults("200")["weight"] == 100.0 / 6.0
    assert AnalysisSettingsStore.defaults("trend")["weight"] == 100.0 / 15.0
    assert AnalysisSettingsStore.defaults("999")["weight"] == 0.0
    with temp_db() as db:
        store = AnalysisSettingsStore(db)
        row = store.get(*SCOPE, "350")
        assert row["weight"] == 100.0 / 6.0 and row["required_depth"] == 60.0, row
        # A section weight edit stays inside the section row: Q2's equal
        # redistribution covers the fifteen analyzers only.
        store.update(*SCOPE, "200", {"weight": 30}, changed_by="owner",
                     command_id="sec-1", changed_at=1.0)
        rows = fetch(store.path, "SELECT COUNT(*) AS n FROM analysis_settings")
        assert rows[0]["n"] == 1, rows
    print("OK - each section defaults to 100/6; section edits do not touch analyzers")


def main():
    tests = [
        test_path_column_migration_on_old_database,
        test_owner_example_40_20_20_20,
        test_sum_stays_100_after_any_edit,
        test_paths_are_independent,
        test_section_defaults_are_equal_sixth,
    ]
    failed = []
    for test in tests:
        print("\n--- %s ---" % test.__name__)
        try:
            test()
        except AssertionError as exc:
            failed.append((test.__name__, str(exc)))
            print("FAILED: %s: %s" % (test.__name__, exc))
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failed.append((test.__name__, repr(exc)))
            print("ERROR: %s: %r" % (test.__name__, exc))
    print("\n" + "=" * 60)
    if failed:
        print("FAILED %d of %d" % (len(failed), len(tests)))
        sys.exit(1)
    print("ALL TESTS PASSED (%d/%d)" % (len(tests), len(tests)))


if __name__ == "__main__":
    main()
