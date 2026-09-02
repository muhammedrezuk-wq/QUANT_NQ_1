#!/usr/bin/env python3
"""Independent before/after proof for the official-time contract.

The same twelve nodes are run one by one so one collection failure cannot hide
later evidence.  On the sealed input snapshot the seven inherited NTP safety
invariants pass and the five new contract nodes fail; on the repaired tree all
twelve must pass.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    "tests/test_time_legacy_invariants.py::test_608_keeps_central_udp_transport",
    "tests/test_time_legacy_invariants.py::test_608_does_not_open_a_private_socket",
    "tests/test_time_legacy_invariants.py::test_608_keeps_exact_ntp_packet_length_validation",
    "tests/test_time_legacy_invariants.py::test_608_keeps_server_mode_validation",
    "tests/test_time_legacy_invariants.py::test_608_keeps_stratum_validation",
    "tests/test_time_legacy_invariants.py::test_608_keeps_originate_timestamp_validation",
    "tests/test_time_legacy_invariants.py::test_608_keeps_absolute_offset_bound",
    "tests/test_clock_module.py::test_rejects_non_finite_stale_bound_and_wrong_writer",
    "tests/test_clock_module.py::test_now_never_moves_backward_and_slews",
    "atoms/608_مزامنة_الوقت/tests/test_atom.py::test_sync_publishes_sample_state_event",
    "atoms/806_نبضة_الوقت/tests/test_atom.py::test_stall_emits_once_with_missed_intervals",
    "tests/test_time_contract_end_to_end.py::test_901_executes_after_003_is_stopped",
]


def run(cases: list[str], label: str) -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    passed = 0
    failed: list[str] = []
    for case in cases:
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
    print(f"{label}={passed} PASS {len(failed)} FAIL")
    return 0 if not failed else 1


def main() -> int:
    return run(CASES, "PROOF_TIME")


if __name__ == "__main__":
    raise SystemExit(main())
