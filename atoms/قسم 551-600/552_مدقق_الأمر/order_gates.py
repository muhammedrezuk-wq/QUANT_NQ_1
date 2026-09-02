from __future__ import annotations

import time
from typing import Any

from shared.financial_scope import text

from order_validation import _neutral_pair_contract

# Campaign 450-901 batch B: the OPEN-path gates (parent authority, margin
# verdict, snapshot validity) extracted verbatim -- same behavior, smaller atom.
#
# Item 25 (27-atom review, 2026-08-27): found completely unwired -- atom.py
# had kept its own inline copy of every check below instead of calling out to
# this module. Fixed here first, with the same rigor as live code, since a
# "wire this in" pass would otherwise trust code that already looks correct
# without re-diffing it against atom.py. Wired into atom.py (v5.5.0) only
# after the fix was proven behavior-preserving: the atom's own 11-scenario
# suite plus a 90-case old-vs-new differential simulation (9 contexts x 10
# order shapes) matched exactly, run outside the project's own test tooling.
# atom.py's OPEN-path block in `_on_built` now delegates to
# `run_open_gates()` below instead of duplicating these three checks inline.
#
# One real bug was closed before wiring: the margin-verdict lookup used the RAW
# `body.get("account_id")` as half of its registry key, while every write to
# that same registry (atom.py's `_on_margin_verdict`, and this file's own
# `on_margin_verdict` in state_inputs.py) stores the key through
# `text(...)` (str(), stripped, "" for None). In atom.py's real call order
# this coincidentally matched, because `_on_built` already normalizes
# `body["account_id"]` before reaching these gates -- but nothing in this
# file enforced that precondition, so a caller that invoked this gate before
# that normalization (or passed a differently-shaped body) would silently
# miss an approved verdict and reject a legitimate order with
# MARGIN_VERDICT_MISSING. Wrapped in text() to match the registry's actual
# key shape unconditionally, the same as every other lookup in this atom.

AUTHORITY_FIELDS = ("decision_id", "parent_decision_id", "owner_command_id")


STAGE_PARENT = "PARENT_DECISION"
STAGE_MARGIN = "MARGIN_VERDICT"
STAGE_SNAPSHOT = "SNAPSHOT_VALIDITY"
SNAPSHOT_USABLE_STATUS = "READY"


async def run_open_gates(atom, body: dict[str, Any],
                         authority_fields: tuple = AUTHORITY_FIELDS) -> str:
    """Empty string = all OPEN gates passed; else the refusal reason."""
    if not _neutral_pair_contract(body) \
            and not any(text(body.get(field)) for field in authority_fields):
        atom._parent_decision_blocked += 1
        await atom._refuse(body, "PARENT_DECISION_MISSING", STAGE_PARENT,
                           measured_at=time.time())
        return "PARENT_DECISION_MISSING"
    verdict = atom._margin_verdicts.get(
        (text(body.get("account_id")), text(body.get("request_id"))))
    if verdict is None:
        atom._margin_verdict_blocked += 1
        await atom._refuse(body, "MARGIN_VERDICT_MISSING", STAGE_MARGIN,
                           measured_at=time.time())
        return "MARGIN_VERDICT_MISSING"
    if not verdict.get("approved"):
        atom._margin_verdict_blocked += 1
        await atom._refuse(body, "MARGIN_VERDICT_REJECTED", STAGE_MARGIN,
                           value=verdict.get("required_margin"),
                           threshold=verdict.get("free_margin"),
                           measured_at=verdict.get("measured_at"))
        return "MARGIN_VERDICT_REJECTED"
    snapshot_id = text(body.get("snapshot_id"))
    if snapshot_id:
        record = atom._snapshots.get(snapshot_id)
        if record is None:
            atom._snapshot_validity_blocked += 1
            await atom._refuse(body, "SNAPSHOT_UNKNOWN", STAGE_SNAPSHOT,
                           measured_at=time.time())
            return "SNAPSHOT_UNKNOWN"
        if not record.get("usable_for_new_exposure"):
            atom._snapshot_validity_blocked += 1
            await atom._refuse(body, "SNAPSHOT_NOT_USABLE", STAGE_SNAPSHOT,
                               value=record.get("snapshot_status"),
                               threshold=SNAPSHOT_USABLE_STATUS,
                               measured_at=record.get("measured_at"))
            return "SNAPSHOT_NOT_USABLE"
    return ""
