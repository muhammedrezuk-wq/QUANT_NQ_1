#!/usr/bin/env python3
from governance.scripts.proof_support import run_cases
CASES=[
'atoms/702_مخزن_الصفقات/tests/test_atom.py::test_stores_opened_from_platform_event',
'atoms/702_مخزن_الصفقات/tests/test_atom.py::test_dedupe_by_source_row',
'atoms/702_مخزن_الصفقات/tests/test_atom.py::test_close_pnl_then_enrich',
'atoms/702_مخزن_الصفقات/tests/test_atom.py::test_partial_kind',
'atoms/702_مخزن_الصفقات/tests/test_atom.py::test_ignores_unknown_event_type',
'atoms/701_مخزن_بيانات_السوق/tests/test_atom.py::test_persists_price_to_disk',
'atoms/701_مخزن_بيانات_السوق/tests/test_atom.py::test_buffers_until_flush_size',
'atoms/701_مخزن_بيانات_السوق/tests/test_atom.py::test_prune_on_day',
'atoms/704_مخزن_الخط_الزمني/tests/test_atom.py::test_records_named_events',
'atoms/704_مخزن_الخط_الزمني/tests/test_atom.py::test_only_watched_events',
'atoms/708_سجل_الرموز/tests/test_atom.py::test_resolves_aliases',
'atoms/708_سجل_الرموز/tests/test_atom.py::test_resolve_request_response',
'atoms/716_تنظيف_التكرار/tests/test_atom.py::test_removes_duplicates_keeps_one',
'tests/test_stores_contract.py::test_01_periodic_flush_survives_without_stop',
'tests/test_stores_contract.py::test_02_critical_timeline_event_is_immediate',
'tests/test_stores_contract.py::test_03_prune_catches_up_without_sysday',
'tests/test_stores_contract.py::test_09_symbol_specs_survive_restart',
'tests/test_stores_contract.py::test_11_dedupe_failure_does_not_advance_checkpoint']
if __name__=='__main__':raise SystemExit(run_cases(CASES,'PROOF_STORES'))
