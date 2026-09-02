#!/usr/bin/env python3
"""Sixteen-case before/after proof for 800/802/803."""
from governance.scripts.proof_support import run_cases

CASES = [
    "atoms/800_النسخ_الاحتياطي/tests/test_atom.py::test_backup_on_day",
    "atoms/800_النسخ_الاحتياطي/tests/test_atom.py::test_interval_skip",
    "atoms/800_النسخ_الاحتياطي/tests/test_atom.py::test_retention",
    "atoms/800_النسخ_الاحتياطي/tests/test_atom.py::test_health_states",
    "atoms/802_أرشفة_الملفات/tests/test_atom.py::test_archives_old_files_only",
    "atoms/802_أرشفة_الملفات/tests/test_atom.py::test_no_old_files",
    "atoms/802_أرشفة_الملفات/tests/test_atom.py::test_interval_skip",
    "atoms/802_أرشفة_الملفات/tests/test_atom.py::test_health_states",
    "atoms/803_تنظيف_الملفات/tests/test_atom.py::test_deletes_old_matching_only",
    "atoms/803_تنظيف_الملفات/tests/test_atom.py::test_no_patterns_deletes_nothing",
    "atoms/803_تنظيف_الملفات/tests/test_atom.py::test_interval_skip",
    "atoms/803_تنظيف_الملفات/tests/test_atom.py::test_health_states",
    "tests/test_backup_archive_contract.py::test_10_backup_retention_under_pressure",
    "tests/test_backup_archive_contract.py::test_11_missing_backup_source_is_announced",
    "tests/test_backup_archive_contract.py::test_01_catchup_runs_without_sysday_after_two_days",
    "tests/test_backup_archive_contract.py::test_07_cleanup_zero_days_deletes_nothing_and_degrades",
]

if __name__ == "__main__":
    raise SystemExit(run_cases(CASES, "PROOF_BACKUP"))
