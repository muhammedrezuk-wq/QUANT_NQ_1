from __future__ import annotations

import json
from pathlib import Path

import yaml

from build_registry.paths import RegistryAtomRoot
from governance.scripts import validate_atoms as validator


ROOT = Path(__file__).resolve().parents[1]
ATOM_ROOT = RegistryAtomRoot(ROOT)


def test_language_contract_ignores_comments_docstrings_and_human_messages() -> None:
    language_errors = []
    for manifest_path in ATOM_ROOT.rglob("manifest.yaml"):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        source_path = manifest_path.parent / "atom.py"
        if not source_path.is_file():
            continue
        tree = validator.ast.parse(source_path.read_text(encoding="utf-8"))
        language_errors.extend(validator._executable_language_findings(tree, source_path.read_text(encoding="utf-8")))
    assert language_errors == []


def test_all_reported_numeric_values_are_owned_without_moving_them() -> None:
    numeric_ids = {151, 152, 153, 154, 155, 162, 163, 164, 201, 262, 264, 618, 832, 870}
    for atom_id in numeric_ids:
        directory = next(path for path in ATOM_ROOT.iterdir() if path.name.startswith(f"{atom_id}_"))
        source = (directory / "atom.py").read_text(encoding="utf-8")
        numbers = validator.literal_numbers(validator.ast.parse(source))
        owned = validator._owned_numeric_values(atom_id)
        # 2026-08-28 (دمج الشريك): بعض الذرّات صارت ثوابتَ مسماة بالكامل فلا
        # أرقام حرفية — الشرط الملكيةُ لا الوجود.
        assert all(item[2] in owned for item in numbers)


def test_validator_json_leaves_only_real_quality_errors() -> None:
    import subprocess
    result = subprocess.run(
        ["python", "governance/scripts/validate_atoms.py", "--json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["errors"] == 0
    assert not [item for item in payload["findings"] if item["severity"] == "ERROR"]
