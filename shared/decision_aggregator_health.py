from __future__ import annotations

from typing import Any

from core.contracts.atom import HealthState, HealthStatus


def health(atom: Any) -> HealthStatus:
    if not atom._running:
        return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
    details = {
        "ticks": atom._ticks_seen, "emitted": atom._emitted,
        "complete_emissions": atom._on_completion,
        "evidence_scopes": len(atom._evidence_store),
        "evidence_sources": sum(len(rows) for rows in atom._evidence_store.values()),
        "updates": atom._updates, "invalid": atom._invalid,
        "section_live_received": atom._section_live_received,
        "section_live_ready": atom._section_live_admitted,
        "section_live_rejected": dict(atom._section_live_rejected),
        "room_sections": sum(len(scope) for scope in atom._room.values()),
        "room_updates": atom._room_updates, "room_emitted": atom._room_emitted,
        "expected_families": list(atom._expected),
    }
    details["missing_by_family"] = dict(sorted(
        atom._missing_family_counts.items(), key=lambda item: -item[1]
    ))
    if not atom._ticks_seen:
        return HealthStatus(
            state=HealthState.DEGRADED, message="NO_CYCLES_YET", details=details
        )
    new_invalid = atom._invalid - atom._health_seen["invalid"]
    atom._health_seen = {"invalid": atom._invalid}
    top_missing = "-"
    if atom._missing_family_counts:
        family, count = max(
            atom._missing_family_counts.items(), key=lambda item: item[1]
        )
        top_missing = "%s=%d" % (family, count)
    state = HealthState.DEGRADED if new_invalid else HealthState.HEALTHY
    # v3.0.0: superseded is GONE by design -- emissions are continuous room
    # snapshots; "complete" counts emissions where every expected family had
    # fresh evidence. The old counters (superseded/late/duplicates) died with
    # the batch model they measured.
    return HealthStatus(
        state=state,
        details=details,
        message=(
            "emitted=%d complete=%d room=%d top_missing=%s new_invalid=%d"
        ) % (
            atom._emitted, atom._on_completion,
            details["room_sections"], top_missing, new_invalid,
        ),
    )
