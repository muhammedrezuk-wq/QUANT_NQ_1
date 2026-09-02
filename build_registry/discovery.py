"""Recursive discovery and classification used by BuildRegistry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.manifest_loader import DiscoveryFailure, scan

from .models import BuildSource, ComponentRecord, IntegritySnapshot, RootSpec

# This is a classification rule for the existing control-boundary numbering
# convention. It changes no IDs and is not a build-count constant.
_CONTROL_SUFFIX = 901
_EXECUTION_PREFIXES = ("execution.", "trading.", "broker.")


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    atom_roots: tuple[RootSpec, ...]
    shared: tuple[ComponentRecord, ...]
    governance_files: tuple[ComponentRecord, ...]
    forex_all: tuple[ComponentRecord, ...]
    crypto_all: tuple[ComponentRecord, ...]
    manifests: tuple[ComponentRecord, ...]
    build_sources: tuple[BuildSource, ...]
    core_version: str | None
    integrity: IntegritySnapshot


def discover_project(project_root: Path) -> DiscoveryResult:
    root = project_root.resolve()
    forex_root = root / "atoms"
    crypto_root = root / "atoms_crypto"
    atom_roots = (
        RootSpec("forex", str(forex_root), "atom_root"),
        RootSpec("crypto", str(crypto_root), "atom_root"),
        RootSpec("shared", str(root / "shared"), "shared_root"),
        RootSpec("governance", str(root / "governance"), "governance_root"),
    )

    forex, forex_failures = _discover_atoms(forex_root, "forex")
    crypto, crypto_failures = _discover_atoms(crypto_root, "crypto")
    all_failures = (
        *forex_failures,
        *crypto_failures,
        *_orphan_atom_files(forex_root),
        *_orphan_atom_files(crypto_root),
    )
    manifests = tuple(sorted((*forex, *crypto), key=lambda item: item.component_id))

    shared = _discover_files(root / "shared", "shared")
    governance_files = _discover_files(root / "governance", "governance")
    sources = _discover_build_sources(root)
    core_version = _discover_core_version(root)
    integrity = _integrity(root, all_failures)
    return DiscoveryResult(
        atom_roots=atom_roots,
        shared=shared,
        governance_files=governance_files,
        forex_all=tuple(sorted(forex, key=lambda item: item.component_id)),
        crypto_all=tuple(sorted(crypto, key=lambda item: item.component_id)),
        manifests=manifests,
        build_sources=tuple(sources),
        core_version=core_version,
        integrity=integrity,
    )


def _discover_atoms(root: Path, scope: str) -> tuple[tuple[ComponentRecord, ...], tuple[DiscoveryFailure, ...]]:
    report = scan(root)
    records: list[ComponentRecord] = []
    for discovered in report.atoms:
        manifest = discovered.manifest
        publishes = tuple(str(item) for item in manifest.publishes)
        subscribes = tuple(str(item) for item in manifest.subscribes)
        metadata = tuple(sorted((str(key), str(value)) for key, value in manifest.metadata.items()))
        event_names = (*publishes, *subscribes)
        capabilities: set[str] = set()
        if any(name.startswith(_EXECUTION_PREFIXES) for name in event_names):
            capabilities.add("execution_event_participant")
        if any(name.startswith("execution.") for name in publishes):
            capabilities.add("execution_publisher")
        if manifest.metadata.get("executable") is True:
            capabilities.add("executable")
        if manifest.id % 1000 == _CONTROL_SUFFIX:
            capabilities.add("governance_control_boundary")
        record = ComponentRecord(
            component_id=f"atom:{scope}:{manifest.id}",
            kind="atom",
            scope=scope,
            path=str(discovered.directory.resolve()),
            manifest_path=str((discovered.directory / "manifest.yaml").resolve()),
            atom_id=manifest.id,
            name=manifest.name,
            version=str(manifest.version),
            core_version=str(manifest.core_version),
            entrypoint=manifest.entrypoint,
            startup_mode=getattr(manifest.startup_mode, "value", str(manifest.startup_mode)),
            critical=manifest.critical,
            dependencies=tuple(sorted(dep.id for dep in manifest.dependencies)),
            capabilities=tuple(sorted(capabilities)),
            publishes=publishes,
            subscribes=subscribes,
            metadata=metadata,
        )
        records.append(record)
    return tuple(records), tuple(report.failures)


def _orphan_atom_files(root: Path) -> tuple[DiscoveryFailure, ...]:
    """Detect executable atom files that have no sibling manifest."""
    if not root.exists():
        return ()
    failures: list[DiscoveryFailure] = []
    for path in sorted(root.rglob("atom.py")):
        if not (path.parent / "manifest.yaml").is_file():
            failures.append(DiscoveryFailure(
                path=path,
                error="atom.py موجود بلا manifest.yaml شقيق — مكوّن غير قابل للتعرّف",
            ))
    return tuple(failures)


def _discover_files(root: Path, scope: str) -> tuple[ComponentRecord, ...]:
    if not root.exists():
        return ()
    records: list[ComponentRecord] = []
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        if any(part in {"__pycache__", "node_modules", "dist", "build"} for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        records.append(ComponentRecord(
            component_id=f"{scope}:file:{relative}",
            kind="file",
            scope=scope,
            path=str(path.resolve()),
        ))
    return tuple(records)


def _discover_build_sources(root: Path) -> list[BuildSource]:
    sources: list[BuildSource] = []
    package = root / "governance" / "PACKAGE_BUILD.txt"
    if package.is_file():
        lines = package.read_text(encoding="utf-8", errors="replace").splitlines()
        if lines and lines[0].strip():
            sources.append(BuildSource(
                path=str(package.relative_to(root)),
                field="build_id",
                value=lines[0].strip(),
                source_kind="package_build",
                status="legacy_candidate",
                evidence="first line of PACKAGE_BUILD.txt",
            ))
    release = root / "config" / "unified_release.json"
    if release.is_file():
        try:
            data = json.loads(release.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get("release"):
            sources.append(BuildSource(
                path=str(release.relative_to(root)),
                field="release",
                value=str(data["release"]),
                source_kind="unified_release_label",
                status="current_candidate_untyped",
                evidence="release label",
            ))
        if data.get("build_id"):
            sources.append(BuildSource(
                path=str(release.relative_to(root)),
                field="build_id",
                value=str(data["build_id"]),
                source_kind="unified_release_build_id",
                status="current_candidate",
                evidence="explicit build_id",
            ))
        if data.get("core_version"): 
            sources.append(BuildSource(
                path=str(release.relative_to(root)),
                field="core_version",
                value=str(data["core_version"]),
                source_kind="unified_release_metadata",
                status="observed",
            ))

    contract = root / "config" / "build_contract.json"
    if contract.is_file():
        try:
            contract_data = json.loads(contract.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            contract_data = {}
        if contract_data.get("build_id"):
            sources.append(BuildSource(
                path=str(contract.relative_to(root)),
                field="build_id",
                value=str(contract_data["build_id"]),
                source_kind="approved_build_contract",
                status="approved" if contract_data.get("status") == "approved" else "unresolved",
                evidence="owner-approved contract",
            ))
        for legacy in contract_data.get("legacy_build_ids", []) or []:
            sources.append(BuildSource(
                path=str(contract.relative_to(root)),
                field="legacy_build_id",
                value=str(legacy),
                source_kind="approved_build_contract_legacy",
                status="legacy",
                evidence="explicitly retained legacy identifier",
            ))

    project_id_pattern = re.compile(r'PROJECT_BUILD_ID\s*=\s*["\']([^"\']+)["\']')
    for path in sorted(root.glob("atoms/**/atom.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = project_id_pattern.search(text)
        if match:
            sources.append(BuildSource(
                path=str(path.relative_to(root)),
                field="PROJECT_BUILD_ID",
                value=match.group(1),
                source_kind="embedded_atom_metadata",
                status="legacy_embedded",
                evidence="embedded in atom implementation; not authoritative",
            ))
    return sources


def _discover_core_version(root: Path) -> str | None:
    version_file = root / "core" / "__version__.py"
    if version_file.is_file():
        match = re.search(r'^CORE_VERSION\s*=\s*["\']([^"\']+)', version_file.read_text(encoding="utf-8", errors="ignore"), re.MULTILINE)
        if match:
            return match.group(1)
    return None


def _integrity(root: Path, failures: tuple[DiscoveryFailure, ...]) -> IntegritySnapshot:
    lock = root / "core" / "CORE.lock"
    lock_present = lock.is_file()
    digest = None
    file_count = None
    if lock_present:
        try:
            data: dict[str, Any] = json.loads(lock.read_text(encoding="utf-8"))
            digest = str(data.get("root_digest")) if data.get("root_digest") else None
            file_count = int(data["file_count"]) if data.get("file_count") is not None else None
        except (OSError, ValueError, TypeError):
            pass
    required_roots = {
        "forex": root / "atoms",
        "crypto": root / "atoms_crypto",
        "shared": root / "shared",
        "governance": root / "governance",
    }
    missing_roots = tuple(sorted(name for name, path in required_roots.items() if not path.is_dir()))
    duplicate_ids: list[tuple[str, int]] = []
    discovery_failures: list[tuple[str, str]] = []
    for failure in failures:
        text = str(failure.error)
        if "Atom ID مكرر:" in text:
            match = re.search(r"Atom ID مكرر:\s*(\d+)", text)
            if match:
                duplicate_ids.append((str(failure.path), int(match.group(1))))
        discovery_failures.append((str(failure.path), text))
    return IntegritySnapshot(
        core_lock_present=lock_present,
        core_lock_root_digest=digest,
        core_lock_file_count=file_count,
        missing_roots=missing_roots,
        duplicate_ids=tuple(sorted(set(duplicate_ids))),
        discovery_failures=tuple(sorted(discovery_failures)),
    )
