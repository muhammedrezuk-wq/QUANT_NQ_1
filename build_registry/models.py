"""Immutable data contracts emitted by the central build registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RootSpec:
    """A discovered project root. No atom count is stored in a root contract."""

    name: str
    path: str
    kind: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "path": self.path, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class ComponentRecord:
    """A manifest-backed atom or a source-tree component reference."""

    component_id: str
    kind: str
    scope: str
    path: str
    manifest_path: str | None = None
    atom_id: int | None = None
    name: str | None = None
    version: str | None = None
    core_version: str | None = None
    entrypoint: str | None = None
    startup_mode: str | None = None
    critical: bool | None = None
    dependencies: tuple[int, ...] = ()
    capabilities: tuple[str, ...] = ()
    publishes: tuple[str, ...] = ()
    subscribes: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "kind": self.kind,
            "scope": self.scope,
            "path": self.path,
            "manifest_path": self.manifest_path,
            "atom_id": self.atom_id,
            "name": self.name,
            "version": self.version,
            "core_version": self.core_version,
            "entrypoint": self.entrypoint,
            "startup_mode": self.startup_mode,
            "critical": self.critical,
            "capabilities": list(self.capabilities),
            "publishes": list(self.publishes),
            "subscribes": list(self.subscribes),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class BuildSource:
    """One piece of build identity evidence; no source is silently selected."""

    path: str
    field: str
    value: str
    source_kind: str
    status: str
    evidence: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "field": self.field,
            "value": self.value,
            "source_kind": self.source_kind,
            "status": self.status,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class IntegritySnapshot:
    """Structural integrity facts collected without changing the tree."""

    core_lock_present: bool
    core_lock_root_digest: str | None
    core_lock_file_count: int | None
    missing_roots: tuple[str, ...] = ()
    duplicate_ids: tuple[tuple[str, int], ...] = ()
    discovery_failures: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "core_lock_present": self.core_lock_present,
            "core_lock_root_digest": self.core_lock_root_digest,
            "core_lock_file_count": self.core_lock_file_count,
            "missing_roots": list(self.missing_roots),
            "duplicate_ids": [list(item) for item in self.duplicate_ids],
            "discovery_failures": [list(item) for item in self.discovery_failures],
        }


@dataclass(frozen=True, slots=True)
class BuildSnapshot:
    """Complete point-in-time result of BuildRegistry discovery."""

    project_root: str
    build_id: str | None
    build_contract_status: str
    build_sources: tuple[BuildSource, ...]
    core_version: str | None
    atom_roots: tuple[RootSpec, ...]
    shared: tuple[ComponentRecord, ...]
    forex: tuple[ComponentRecord, ...]
    crypto: tuple[ComponentRecord, ...]
    governance: tuple[ComponentRecord, ...]
    forex_all: tuple[ComponentRecord, ...]
    crypto_all: tuple[ComponentRecord, ...]
    manifests: tuple[ComponentRecord, ...]
    atom_ids: tuple[tuple[str, int], ...]
    execution_targets: tuple[ComponentRecord, ...]
    integrity: IntegritySnapshot
    refreshed_at: float

    def find_atom(self, atom_id: int, scope: str | None = None) -> tuple[ComponentRecord, ...]:
        return tuple(
            record
            for record in self.manifests
            if record.atom_id == atom_id and (scope is None or record.scope == scope)
        )

    def find_execution_target(self, atom_id: int, scope: str | None = None) -> tuple[ComponentRecord, ...]:
        return tuple(
            record
            for record in self.execution_targets
            if record.atom_id == atom_id and (scope is None or record.scope == scope)
        )

    def as_dict(self, *, include_components: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "project_root": self.project_root,
            "build_id": self.build_id,
            "build_contract_status": self.build_contract_status,
            "build_sources": [item.as_dict() for item in self.build_sources],
            "core_version": self.core_version,
            "atom_roots": [item.as_dict() for item in self.atom_roots],
            "counts": {
                "shared": len(self.shared),
                "forex": len(self.forex),
                "crypto": len(self.crypto),
                "governance": len(self.governance),
                "forex_all": len(self.forex_all),
                "crypto_all": len(self.crypto_all),
                "manifests": len(self.manifests),
                "execution_targets": len(self.execution_targets),
            },
            "atom_ids": [list(item) for item in self.atom_ids],
            "manifests": [item.as_dict() if include_components else item.component_id for item in self.manifests],
            "shared": [item.as_dict() if include_components else item.component_id for item in self.shared],
            "forex": [item.as_dict() if include_components else item.component_id for item in self.forex],
            "crypto": [item.as_dict() if include_components else item.component_id for item in self.crypto],
            "governance": [item.as_dict() if include_components else item.component_id for item in self.governance],
            "execution_targets": [item.as_dict() if include_components else item.component_id for item in self.execution_targets],
            "integrity": self.integrity.as_dict(),
            "refreshed_at": self.refreshed_at,
        }
        return data
