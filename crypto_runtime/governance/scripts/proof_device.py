#!/usr/bin/env python3
from governance.scripts.proof_support import run_cases
CASES=[
'atoms/006_مراقب_التخزين/tests/test_atom.py::test_low_alert_edge_triggered_once',
'atoms/006_مراقب_التخزين/tests/test_atom.py::test_recovered_when_back_healthy',
'atoms/006_مراقب_التخزين/tests/test_atom.py::test_bad_path_reports_unknown_without_crash',
'atoms/007_سلامة_الملفات/tests/test_atom.py::test_baseline_then_detect_modification',
'atoms/007_سلامة_الملفات/tests/test_atom.py::test_detect_missing_file',
'atoms/007_سلامة_الملفات/tests/test_atom.py::test_dir_content_modification_detected',
'atoms/007_سلامة_الملفات/tests/test_atom.py::test_pycache_and_pyc_ignored',
'atoms/007_سلامة_الملفات/tests/test_atom.py::test_dir_added_and_removed_source',
'atoms/007_سلامة_الملفات/tests/test_atom.py::test_snapshot_restore_preserves_security_baseline',
'atoms/007_سلامة_الملفات/tests/test_atom.py::test_no_config_reports_unknown',
'atoms/753_موارد_الجهاز/tests/test_atom.py::test_publishes_raw_metrics_on_pulse',
'atoms/753_موارد_الجهاز/tests/test_atom.py::test_no_interpretation_no_thresholds',
'atoms/753_موارد_الجهاز/tests/test_atom.py::test_health_reports_readings',
'atoms/753_موارد_الجهاز/tests/test_atom.py::test_timestamp_from_pulse_only',
'tests/test_device_contract.py::test_26_device_without_psutil_degrades',
'tests/test_device_legacy_invariants.py::test_device_resource_atom_keeps_raw_fact_contract',
'tests/test_device_contract.py::test_20_first_boot_without_baseline_is_untrusted',
'tests/test_device_contract.py::test_23_storage_alert_uses_pulse_not_health_manager',
'tests/test_device_contract.py::test_24_integrity_violation_uses_pulse_not_health_manager']
if __name__=='__main__':raise SystemExit(run_cases(CASES,'PROOF_DEVICE'))
