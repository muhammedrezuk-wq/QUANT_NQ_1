#!/usr/bin/env python3
"""Fifteen-case before/after proof for archive compression and integrity."""
from governance.scripts.proof_support import run_cases

CASES = [
    "atoms/715_ضغط_الأرشيف/tests/test_atom.py::test_compresses_archive",
    "atoms/715_ضغط_الأرشيف/tests/test_atom.py::test_skips_when_no_rows",
    "atoms/715_ضغط_الأرشيف/tests/test_atom.py::test_below_min_size",
    "atoms/715_ضغط_الأرشيف/tests/test_atom.py::test_delete_original_when_configured",
    "atoms/715_ضغط_الأرشيف/tests/test_atom.py::test_health_states",
    "atoms/717_سلامة_البيانات/tests/test_atom.py::test_sound_when_clean",
    "atoms/717_سلامة_البيانات/tests/test_atom.py::test_flags_future_and_missing_stamp",
    "atoms/717_سلامة_البيانات/tests/test_atom.py::test_respects_time_column",
    "atoms/717_سلامة_البيانات/tests/test_atom.py::test_health_states",
    "tests/test_backup_legacy_invariants.py::test_legacy_01_missing_compression_source_is_reported",
    "tests/test_backup_legacy_invariants.py::test_legacy_02_zero_rows_does_not_compress",
    "tests/test_backup_legacy_invariants.py::test_legacy_03_integrity_reports_clean_table_sound",
    "tests/test_backup_legacy_invariants.py::test_legacy_04_integrity_identifier_guard_remains_present",
    "tests/test_backup_archive_contract.py::test_13_corrupt_compression_keeps_original",
    "tests/test_backup_archive_contract.py::test_17_rotation_publishes_only_closed_database",
]

if __name__ == "__main__":
    raise SystemExit(run_cases(CASES, "PROOF_715_717"))
