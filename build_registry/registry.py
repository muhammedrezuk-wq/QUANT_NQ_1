"""Central read-only Build Registry facade."""

from __future__ import annotations

import time
from pathlib import Path

from .discovery import discover_project
from .models import BuildSnapshot, ComponentRecord


class BuildRegistry:
    """Owns one recursive build snapshot; it never starts or mutates components."""

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self._snapshot: BuildSnapshot | None = None

    def refresh(self) -> BuildSnapshot:
        result = discover_project(self.project_root)
        governance_atoms = tuple(
            record
            for record in result.manifests
            if "governance_control_boundary" in record.capabilities
        )
        governance_files = tuple(
            record for record in result.governance_files
        )
        governance = tuple(sorted((*governance_files, *governance_atoms), key=lambda item: item.component_id))
        forex = tuple(
            record for record in result.forex_all
            if "governance_control_boundary" not in record.capabilities
        )
        crypto = tuple(
            record for record in result.crypto_all
            if "governance_control_boundary" not in record.capabilities
        )
        execution_targets = tuple(
            record
            for record in result.manifests
            if "execution_event_participant" in record.capabilities
            or "executable" in record.capabilities
        )
        atom_ids = tuple(sorted(
            (record.scope, record.atom_id)
            for record in result.manifests
            if record.atom_id is not None
        ))
        contract_status = _build_contract_status(result.build_sources)
        snapshot = BuildSnapshot(
            project_root=str(self.project_root),
            build_id=_resolved_build_id(result.build_sources) if contract_status == "RESOLVED" else None,
            build_contract_status=contract_status,
            build_sources=result.build_sources,
            core_version=result.core_version,
            atom_roots=result.atom_roots,
            shared=result.shared,
            forex=tuple(sorted(forex, key=lambda item: item.component_id)),
            crypto=tuple(sorted(crypto, key=lambda item: item.component_id)),
            governance=governance,
            forex_all=result.forex_all,
            crypto_all=result.crypto_all,
            manifests=result.manifests,
            atom_ids=atom_ids,
            execution_targets=tuple(sorted(execution_targets, key=lambda item: item.component_id)),
            integrity=result.integrity,
            refreshed_at=time.time(),
        )
        self._snapshot = snapshot
        return snapshot

    def snapshot(self) -> BuildSnapshot:
        return self._snapshot if self._snapshot is not None else self.refresh()

    def find_atom(self, atom_id: int, scope: str | None = None) -> tuple[ComponentRecord, ...]:
        return self.snapshot().find_atom(atom_id, scope)

    def find_execution_target(self, atom_id: int, scope: str | None = None) -> tuple[ComponentRecord, ...]:
        return tuple(
            record for record in self.snapshot().execution_targets
            if record.atom_id == atom_id and (scope is None or record.scope == scope)
        )


def _approved_ids(sources) -> set[str]:
    return {
        source.value for source in sources
        if source.field == "build_id" and source.status == "approved"
    }


def _current_candidate_ids(sources) -> set[str]:
    return {
        source.value for source in sources
        if source.field in {"build_id", "release"}
        and source.status in {"current_candidate", "current_candidate_untyped"}
    }


def _resolved_build_id(sources) -> str | None:  # noqa: ANN001
    """Return only an explicitly approved ID; never infer it from counts."""
    approved = _approved_ids(sources)
    return next(iter(approved)) if len(approved) == 1 and _build_contract_status(sources) == "RESOLVED" else None


def _build_contract_status(sources) -> str:
    approved = _approved_ids(sources)
    candidates = _current_candidate_ids(sources)
    if len(approved) != 1:
        return "UNDECLARED" if not approved else "CONFLICT"
    chosen = next(iter(approved))
    if candidates and candidates != {chosen}:
        return "CONFLICT"
    return "RESOLVED"
