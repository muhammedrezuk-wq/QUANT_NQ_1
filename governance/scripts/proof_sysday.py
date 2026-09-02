#!/usr/bin/env python3
"""Six-case proof for daily-work continuity without relying on SYS_DAY."""
from governance.scripts.proof_support import run_cases

CASES = [
    f"tests/test_backup_sysday_contract.py::test_sysday_{index:02d}_{name}"
    for index, name in [
        (1, "time_pulse_source_has_no_private_wall_clock"),
        (2, "806_restores_bucket_and_emits_current_once"),
        (3, "existing_714_catchup_runs_without_day"),
        (4, "backup_catches_up_without_day"),
        (5, "file_archive_catches_up_without_day"),
        (6, "cleanup_catches_up_without_day"),
    ]
]

if __name__ == "__main__":
    raise SystemExit(run_cases(CASES, "PROOF_SYSDAY"))
