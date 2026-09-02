#!/usr/bin/env python3
"""Run the sixteen regression nodes that expose the former time-contract breaks."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    "tests/test_clock_module.py::test_rejects_non_finite_stale_bound_and_wrong_writer",
    "tests/test_clock_module.py::test_now_never_moves_backward_and_slews",
    "tests/test_clock_module.py::test_quality_transitions_local_synced_stale",
    "tests/test_clock_module.py::test_mono_is_independent_timeout_clock",
    "tests/test_clock_module.py::test_pulse_guard_validates_derived_identity_and_restores",
    "atoms/608_مزامنة_الوقت/tests/test_atom.py::test_sync_publishes_sample_state_event",
    "atoms/608_مزامنة_الوقت/tests/test_atom.py::test_ntp_rejects_zero_packet",
    "atoms/608_مزامنة_الوقت/tests/test_atom.py::test_ntp_rejects_originate_mismatch",
    "atoms/608_مزامنة_الوقت/tests/test_atom.py::test_ntp_rejects_bad_version_and_unsynchronized_leap",
    "atoms/608_مزامنة_الوقت/tests/test_atom.py::test_stop_preserves_failure_evidence",
    "atoms/003_الساعة/tests/test_atom.py::test_sample_updates_shared_clock_and_publishes_approved_state",
    "atoms/003_الساعة/tests/test_atom.py::test_non_finite_and_malformed_samples_are_rejected",
    "atoms/806_نبضة_الوقت/tests/test_atom.py::test_emit_has_stable_pulse_id_and_clock_fields",
    "atoms/806_نبضة_الوقت/tests/test_atom.py::test_stall_emits_once_with_missed_intervals",
    "atoms/111_جسر_الوقت/tests/test_atom.py::test_detects_clock_vs_event_bus_divergence",
    "atoms/552_مدقق_الأمر/tests/test_atom.py::test_unsynced_clock_blocks_open_but_not_management",
]


def main() -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    passed = 0
    failed: list[str] = []
    for case in CASES:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", case],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            passed += 1
            print("PASS", case)
        else:
            failed.append(case)
            print("FAIL", case)
            print(result.stdout)
            print(result.stderr)
    print(f"TIME_BREAKS={passed} PASS {len(failed)} FAIL")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
