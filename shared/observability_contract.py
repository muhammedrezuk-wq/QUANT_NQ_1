# -*- coding: utf-8 -*-
"""Unified observability contract — the intelligence paper (ورقة الذكاء) Phase 1.

The paper §5.2 adds an observability envelope NEXT TO every atom's core
outputs, without changing their meaning. The bus already injects the causal
identity (event_id · trace_id · parent_event_id · timestamp); this contract
covers the remaining fields and the shared health-state vocabulary (§9).

Ten-state vocabulary (§9) — states live in PAYLOADS, mapped at the core
boundary to the core's four sealed states (HEALTHY/DEGRADED/UNHEALTHY/UNKNOWN):
the frozen core is never touched (review decision 2026-08-23).
"""

from __future__ import annotations

from typing import Any, Mapping

# §9 — the ten states. UNKNOWN is NOT neutral: it means "not enough evidence".
HEALTH_STATES = (
    "STARTING", "WARMING", "HEALTHY", "DEGRADED", "STALE",
    "INSUFFICIENT_DATA", "FAILED", "DISABLED", "RECOVERING", "UNKNOWN",
)

# §5.2 — the envelope fields (bus-injected ones marked BUS).
ENVELOPE_FIELDS = (
    "atom_id", "section_id",
    "event_id",            # BUS
    "parent_event_id",     # BUS
    "trace_id",            # BUS
    "timestamp",           # BUS (publish time, corrected clock)
    "source_timestamp",
    "input_quality", "output_quality",
    "calibration_version", "model_version",
    "regime_id", "regime_version",
    "technical_health", "analytical_health", "trading_utility",
    "latency_ms", "resource_class",
)

# Mapping to the sealed core boundary (10 -> 4), for display only.
CORE_STATE_MAP = {
    "STARTING": "UNHEALTHY", "WARMING": "DEGRADED", "HEALTHY": "HEALTHY",
    "DEGRADED": "DEGRADED", "STALE": "DEGRADED", "INSUFFICIENT_DATA": "UNKNOWN",
    "FAILED": "UNHEALTHY", "DISABLED": "UNHEALTHY", "RECOVERING": "DEGRADED",
    "UNKNOWN": "UNKNOWN",
}


def core_state_of(state: str) -> str:
    """Map a payload state to the sealed core's four-state boundary."""
    return CORE_STATE_MAP.get(str(state or "").upper(), "UNKNOWN")


def stamp_observability(card: dict[str, Any], *, atom_id: str, section_id: str,
                        regime_id: str | None = None,
                        calibration_version: str | None = None,
                        latency_ms: float | None = None,
                        resource_class: str = "cold") -> dict[str, Any]:
    """Stamp the §5.2 envelope on a payload COPY — never mutate the input.

    Only measured values are stamped; anything unmeasured is DECLARED by name
    in ``observability_unknown`` — never invented as zero (paper §9 rule).
    """
    out = dict(card)
    envelope: dict[str, Any] = {
        "atom_id": str(atom_id), "section_id": str(section_id),
        "regime_id": regime_id, "calibration_version": calibration_version,
        "latency_ms": latency_ms, "resource_class": resource_class,
    }
    unknown = sorted(name for name, value in envelope.items() if value is None)
    out["observability"] = envelope
    out["observability_unknown"] = unknown
    return out


def envelope_of(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read the envelope from a payload ({} when absent — declared, not faked)."""
    value = payload.get("observability") if isinstance(payload, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}
