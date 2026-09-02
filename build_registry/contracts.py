"""Boundary contracts separating Core boot, atom boot, and release readiness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import BuildSnapshot


@dataclass(frozen=True, slots=True)
class AtomBootResult:
    """Outcome of loading the discovered atom set, independent of Core reachability."""

    booted: tuple[int, ...]
    failed: tuple[int, ...]
    excluded: tuple[int, ...]
    scan_failures: tuple[str, ...]

    @property
    def success(self) -> bool:
        return not self.failed and not self.excluded and not self.scan_failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "booted": list(self.booted),
            "failed": list(self.failed),
            "excluded": list(self.excluded),
            "scan_failures": list(self.scan_failures),
            "success": self.success,
        }


@dataclass(frozen=True, slots=True)
class CoreBootResult:
    """Core boot contract: reaching Bootloader is separate from atom success."""

    reached_bootloader: bool
    core_success: bool
    core_version: str | None
    atom_boot: AtomBootResult | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "reached_bootloader": self.reached_bootloader,
            "core_success": self.core_success,
            "core_version": self.core_version,
            "atom_boot": self.atom_boot.as_dict() if self.atom_boot else None,
            "reason": self.reason,
        }


def core_boot_from_report(report: Any, core_version: str | None = None) -> CoreBootResult:
    """Normalize a BootReport without treating atom exclusions as Core failure."""
    if report is None:
        return CoreBootResult(
            reached_bootloader=False,
            core_success=False,
            core_version=core_version,
            reason="bootloader report unavailable",
        )
    atom_boot = AtomBootResult(
        booted=tuple(report.booted),
        failed=tuple(report.failed),
        excluded=tuple(report.excluded),
        scan_failures=tuple(str(item.error) for item in report.scan_failures),
    )
    reason = report.abort_reason if not atom_boot.success else None
    return CoreBootResult(
        reached_bootloader=True,
        core_success=True,
        core_version=core_version,
        atom_boot=atom_boot,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class ReleaseGateResult:
    """Release contract; unresolved build identity fails closed."""

    status: str
    reasons: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "ok": self.ok, "reasons": list(self.reasons)}


def evaluate_release(snapshot: BuildSnapshot) -> ReleaseGateResult:
    """Evaluate only registry/core structural prerequisites; no trading logic."""
    reasons: list[str] = []
    if snapshot.build_contract_status != "RESOLVED":
        reasons.append(f"BUILD_CONTRACT_{snapshot.build_contract_status}")
    if snapshot.integrity.missing_roots:
        reasons.append("MISSING_ROOTS:" + ",".join(snapshot.integrity.missing_roots))
    if snapshot.integrity.duplicate_ids:
        reasons.append("DUPLICATE_ATOM_IDS")
    if snapshot.integrity.discovery_failures:
        reasons.append("DISCOVERY_FAILURES")
    if not snapshot.integrity.core_lock_present:
        reasons.append("CORE_LOCK_MISSING")
    if not snapshot.core_version:
        reasons.append("CORE_VERSION_UNDECLARED")
    if reasons:
        return ReleaseGateResult("BLOCKED", tuple(reasons))
    return ReleaseGateResult("READY", ())
