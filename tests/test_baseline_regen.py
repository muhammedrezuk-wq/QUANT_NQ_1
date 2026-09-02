from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools import baseline_regen as baseline


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    base = tmp_path / "scope"
    source = base / "src"
    source.mkdir(parents=True)
    (source / "a.py").write_text("A = 1\n", encoding="utf-8")
    (source / "b.py").write_text("B = 2\n", encoding="utf-8")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump({
            "config": {
                "watched_files": [],
                "watched_dirs": ["src"],
                "watched_extensions": [".py"],
                "watched_names": [],
                "min_watched_items": 1,
                "ignored_dir_names": [],
                "ignored_suffixes": [],
            }
        }, sort_keys=False),
        encoding="utf-8",
    )
    cfg = baseline._load_cfg(manifest)
    current = baseline._collect(base, cfg)
    baseline_file = tmp_path / "integrity_baseline.json"
    baseline_file.write_text(
        json.dumps({
            "format": "quant-nq-integrity-baseline",
            "scope_digest": baseline._scope_digest(cfg),
            "items": current,
        }),
        encoding="utf-8",
    )
    return base, manifest, baseline_file


def test_check_accepts_an_exact_baseline(tmp_path: Path) -> None:
    base, manifest, baseline_file = _fixture(tmp_path)
    assert baseline.regenerate(base, manifest, baseline_file, write=False)


@pytest.mark.parametrize("change", ["stale", "added", "removed", "scope"])
def test_check_rejects_every_kind_of_integrity_drift(
    tmp_path: Path, change: str,
) -> None:
    base, manifest, baseline_file = _fixture(tmp_path)
    if change == "stale":
        (base / "src" / "a.py").write_text("A = 9\n", encoding="utf-8")
    elif change == "added":
        (base / "src" / "c.py").write_text("C = 3\n", encoding="utf-8")
    elif change == "removed":
        (base / "src" / "b.py").unlink()
    else:
        payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        payload["config"]["min_watched_items"] = 2
        manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    assert not baseline.regenerate(base, manifest, baseline_file, write=False)


def test_crypto_guard_manifest_uses_the_sectioned_runtime_tree() -> None:
    _base, manifest, _baseline_file = baseline.SCOPES[2]
    assert manifest.is_file(), manifest
    assert "قسم 2001-2050" in manifest.as_posix()
