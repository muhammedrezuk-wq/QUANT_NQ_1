#!/usr/bin/env python3
from governance.scripts.proof_support import run_cases
CASES=[
"tests/test_market_decision_path.py::test_invalid_ticks_are_rejected[bad0]",
"tests/test_market_decision_path.py::test_invalid_ticks_are_rejected[bad1]",
"tests/test_market_decision_path.py::test_invalid_ticks_are_rejected[bad2]",
"tests/test_market_decision_path.py::test_invalid_ticks_are_rejected[bad3]",
"tests/test_market_decision_path.py::test_invalid_ticks_are_rejected[bad4]",
"tests/test_market_decision_path.py::test_invalid_ticks_are_rejected[bad5]",
"tests/test_market_decision_path.py::test_valid_contract_is_flat_and_identical",
"tests/test_market_decision_path.py::test_full_tick_to_candle_path",
"tests/test_market_decision_path.py::test_receivers_only_use_validated_channel",
"tests/test_market_decision_path.py::test_quality_monitor_stays_on_raw_and_sees_corruption",
"tests/test_market_decision_path.py::test_validator_stop_is_announced",
"tests/test_market_decision_path.py::test_calendar_restart_keeps_future_and_drops_expired",
"tests/test_market_decision_path.py::test_calendar_restore_failure_is_unknown",
"tests/test_market_decision_path.py::test_feed_states_never_seen_dead_recovered",
"tests/test_market_decision_path.py::test_position_specs_are_account_broker_scoped",
"tests/test_market_decision_path.py::test_missing_specs_is_explicit_rejection",
"tests/test_market_decision_path.py::test_below_min_volume_rejected_not_raised",
"tests/test_market_decision_path.py::test_113_114_are_not_decision_route",
]
if __name__=="__main__":raise SystemExit(run_cases(CASES,"PROOF_MARKET_DECISION"))
