"""Structural regression guard for approved solution papers 9 and 10."""
from __future__ import annotations

import sys

from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

ATOMS=ATOM_ROOT


def source(atom_id:int)->str:
    return (next(ATOMS.glob(f"{atom_id}_*"))/"atom.py").read_text()


def manifest(atom_id:int)->dict:
    return yaml.safe_load((next(ATOMS.glob(f"{atom_id}_*"))/"manifest.yaml").read_text())


def main()->int:
    s508=source(508);s513=source(513);s517=source(517);s524=source(524);s552=source(552)
    s563=source(563);s578=source(578);s579=source(579);s609=source(609);s611=source(611);s618=source(618)
    checks={
        "609 age health": all(token in s609 for token in ("POSITIONS_STALE","stale_after_s","_last_read_at","HealthState.UNKNOWN")),
        "609 financial query truth": all(token in s609 for token in ("ACCOUNT_ID_UNAVAILABLE","COMMISSION_UNAVAILABLE","SCHEMA_UNAVAILABLE","unknown_positions")),
        "609 operational verdict": all(token in s609 for token in ("usable_for_new_exposure","usable_for_protection","INCOMPLETE","STALE")),
        "578 position picture gate": "_position_picture_blocked" in s578 and "usable_for_protection" in s578,
        "579 explicit state machine": all(token in s579 for token in ("STATUS_EXECUTING","STATUS_FAILED","STATUS_SUCCESS","attempts","last_attempt_at")),
        "579 continuity": "snapshot_state" in s579 and "restore_state" in s579 and "asset.extraction.retry_requested" in manifest(579)["subscribes"],
        "524 consumes failure": "asset.extraction.failed" in manifest(524)["subscribes"] and "failure_reason" in s524,
        "517 partial continuity": all(token in s517 for token in ("PARTIAL","_watchdog","remaining_s","reduce_consumer_event","async def snapshot","async def restore")),
        "late cost source revisions": "_pending_cost_rows" in s611 and "revision_id" in s563 and "trade_identity" in s563,
        "508 unknown position": all(token in s508 for token in ("UNKNOWN_POSITION","_unknown_positions","usable_for_new_exposure")),
        "unknown exposure gate": "risk.exposure.state" in manifest(552)["subscribes"] and "EXPOSURE_STATE_NOT_USABLE" in s552,
        "scoped spec freshness": all(token in s513 for token in ("specs_max_age_s","received_monotonic","SIZING_UNAVAILABLE_FOR_SYMBOL","STALE_ACCOUNT_SYMBOL_SPECS")),
        "618 spec timestamps": "spec_published_at" in s618 and "spec_observed_monotonic" in s618,
    }
    manifests=[yaml.safe_load(path.read_text()) for path in ATOMS.glob("*/manifest.yaml")]
    checks["SYS_15MIN explicitly unused"]=(
        '"SYS_15MIN": "UNUSED_EVENT"' in (ROOT/"governance/checks/check_events.py").read_text()
        and not any("SYS_15MIN" in (item.get("subscribes") or []) for item in manifests))
    failed=[name for name,ok in checks.items() if not ok]
    if failed:
        print("PAPERS_9_10_TRUTH_CONTRACT=FAIL")
        for name in failed:print(name)
        return 1
    print("PAPERS_9_10_TRUTH_CONTRACT=PASS")
    return 0


if __name__=="__main__":raise SystemExit(main())
