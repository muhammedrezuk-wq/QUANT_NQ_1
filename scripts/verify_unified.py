"""Verify the unified release through the central Build Registry."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_registry import BuildRegistry, evaluate_release  # noqa: E402


def main() -> int:
    snapshot = BuildRegistry(ROOT).refresh()
    failures: list[str] = []
    if snapshot.integrity.missing_roots:
        failures.append("missing roots: " + ", ".join(snapshot.integrity.missing_roots))
    if snapshot.integrity.duplicate_ids:
        failures.append("duplicate atom IDs detected")
    if snapshot.integrity.discovery_failures:
        failures.append("discovery failures detected")
    if not snapshot.integrity.core_lock_present:
        failures.append("core/CORE.lock is missing")

    seal = subprocess.run(
        [sys.executable, str(ROOT / "governance/scripts/freeze_core.py"), "verify", "--quiet"],
        cwd=ROOT,
    )
    if seal.returncode:
        failures.append("core seal verification failed")

    release = evaluate_release(snapshot)
    if not release.ok:
        failures.extend(release.reasons)

    print(f"Forex root manifests: {len(snapshot.forex_all)}")
    print(f"Crypto root manifests: {len(snapshot.crypto_all)}")
    print(f"Shared components: {len(snapshot.shared)}")
    print(f"Governance components: {len(snapshot.governance)}")
    print(f"Execution targets: {len(snapshot.execution_targets)}")
    print(f"Build contract: {snapshot.build_contract_status}")
    print(f"Build ID: {snapshot.build_id or 'UNRESOLVED'}")
    print(f"Core version: {snapshot.core_version or 'UNDECLARED'}")
    print(f"Core seal: {'OK' if seal.returncode == 0 else 'FAIL'}")
    print(f"Release gate: {release.status}")
    if failures:
        for failure in failures:
            print("FAIL: " + failure)
        return 1
    print("Unified release verification: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
