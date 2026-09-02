"""Structural guard for execution-state truth and continuity paper 8."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

ATOMS=ATOM_ROOT
def source(atom_id):return (next(ATOMS.glob(str(atom_id)+"_*"))/"atom.py").read_text(encoding="utf-8")
def manifest(atom_id):return (next(ATOMS.glob(str(atom_id)+"_*"))/"manifest.yaml").read_text(encoding="utf-8")

def main()->int:
 failures=[];s516=source(516);s517=source(517);s518=source(518);s518d=(next(ATOMS.glob("518_*"))/"durable_ledger_consumer.py").read_text();s550=source(550);s552=source(552);s560=source(560);s563=source(563);s570=source(570);s578=source(578);s583=source(583);s586=source(586);s707=source(707)
 checks={
  "550 final is finalized": 'STAGE_FINALIZED = "DECISION_FINALIZED"' in s550 and '"sent"' not in s550.lower(),
  "550 consumes real stages": all(x in manifest(550) for x in ("platform.brain_signal.written","execution.command.ack","platform.trade_event")),
  "552 counter truthful": "_decisions_finalized" in s552 and "_sent" not in s552,
  "570 emitted not sent": 'status"]="EMITTED"' in s570 and 'status"]="SENT"' not in s570,
  "578 emitted counter truthful": "_order_requests_emitted" in s578 and "self._sent" not in s578,
  "707 no SENT stage": "STAGE_SENT" not in s707 and "DECISION_FINALIZED" in s707,
  "583 completeness": all(x in s583 for x in ("PROTECTION_ONLY","INCOMPLETE","STALE","usable_for_new_exposure","stale_components")),
  "578 consumes snapshot verdict": "usable_for_new_exposure" in s578 and "usable_for_protection" in s578,
  "586 watchdog": "monotonic_deadline" in s586 and "async def _watchdog" in s586,
  "586 continuity": "async def snapshot" in s586 and "async def restore" in s586 and "symbol.resolve.orphaned" in manifest(586),
  "560 no point fallback": "self._points" not in s560 and "point, 1.0" not in s560 and "spec_digest" in s560,
  "563 durable authority": "durable_execution_journal" in s563 and "_MAX_TRACKED_EVENTS" not in s563 and "MISSING_DURABLE_EVENT_ID" in s563,
  "downstream durable consumers": all("durable_execution_journal" in item for item in (s516,s517,s518d)) and all("event_id" in item for item in (s516,s517,s518d)),
  "financial projection transaction": "reduce_consumer_event" in s516 and "reduce_consumer_event" in s518d and "reduce_consumer_event" in s517,
  "durable journal tables": all(x in (ROOT/"shared"/"durable_execution_journal.py").read_text() for x in ("processed_trade_events","execution_request_ledger","execution_outbox","durable_consumer_state","durable_consumer_claims")),
  "consumer claim state outbox atomic": "BEGIN IMMEDIATE" in (ROOT/"shared"/"durable_execution_journal.py").read_text() and "INVALID_CONSUMER_REDUCTION" in (ROOT/"shared"/"durable_execution_journal.py").read_text(),
 }
 for name,ok in checks.items():
  if not ok:failures.append(name)
 if failures:
  print("EXECUTION_STATE_TRUTH_CONTRACT=FAIL")
  for failure in failures:print(failure)
  return 1
 print("EXECUTION_STATE_TRUTH_CONTRACT=PASS")
 return 0
if __name__=="__main__":raise SystemExit(main())
