"""Create/repair the shared-atom links in a unified QUANT_NQ checkout.

Windows uses directory junctions (no administrator privilege is normally
needed); POSIX uses relative symlinks. The original ``atoms/`` tree is never
copied or modified.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "config" / "unified_layout.json"


def _atom_dir(root: Path, atom_id: int) -> Path:
    matches = sorted(root.glob(f"{atom_id:03d}_*"))
    if not matches:
        matches = sorted(root.glob(f"{atom_id}_*"))
    if not matches:
        raise FileNotFoundError(f"atom {atom_id} is missing under {root}")
    return matches[0]


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _make_link(link: Path, target: Path) -> str:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        if link.resolve() == target.resolve():
            return "kept"
        _remove(link)

    relative = os.path.relpath(target, link.parent)
    if os.name == "nt":
        # Junctions work on standard Windows installations without requiring
        # Developer Mode or a SeCreateSymbolicLink privilege.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    else:
        link.symlink_to(relative, target_is_directory=True)
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare unified Forex/Crypto atom links")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    data = json.loads(LAYOUT.read_text(encoding="utf-8"))
    atoms = ROOT / "atoms"
    crypto = ROOT / "atoms_crypto"
    crypto.mkdir(exist_ok=True)
    failures: list[str] = []
    created = kept = 0

    for atom_id in data["shared_links"]:
        try:
            target = _atom_dir(atoms, int(atom_id))
            link = crypto / target.name
            if args.verify_only:
                if not link.exists() or link.resolve() != target.resolve():
                    failures.append(f"{link} -> {target}")
                continue
            result = _make_link(link, target)
            if result == "created":
                created += 1
            else:
                kept += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{atom_id}: {exc}")

    # Relative runtime paths used by the shared atoms (for example 007's
    # watched_dirs and the storage atoms' var/store paths) are namespaced per
    # market working directory. These links expose only the selected market's
    # atom root plus read-only project code; databases still live below each
    # runtime's own working directory.
    runtime_targets = {
        "forex_runtime": "atoms",
        "crypto_runtime": "atoms_crypto",
    }
    runtime_common = [
        "transport", "security", "clock", "catchup", "scripts", "tools",
        "shared", "governance", "config", "mt5", "ctrader", "core",
    ]
    for runtime_name, atom_target in runtime_targets.items():
        runtime = ROOT / runtime_name
        runtime.mkdir(parents=True, exist_ok=True)
        for name in ["atoms", *runtime_common]:
            target = ROOT / (atom_target if name == "atoms" else name)
            link = runtime / name
            try:
                if args.verify_only:
                    if not link.exists() or link.resolve() != target.resolve():
                        failures.append(f"{link} -> {target}")
                else:
                    result = _make_link(link, target)
                    if result == "created":
                        created += 1
                    else:
                        kept += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{link}: {exc}")

    if failures:
        print("Unified-link preparation failed:")
        for failure in failures:
            print("  " + failure)
        return 2
    print(f"Unified links OK: {created} created, {kept} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
