#!/usr/bin/env python3
from governance.scripts.proof_support import run_cases
CASES=[
'atoms/608_مزامنة_الوقت/tests/test_atom.py::test_stale_event_emits_once_per_success',
'atoms/608_مزامنة_الوقت/tests/test_atom.py::test_failure_retry_backoff_sequence']
if __name__=='__main__':raise SystemExit(run_cases(CASES,'PROOF_TIME_EXTENSIONS'))
