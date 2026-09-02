from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_MIRRORS = (ROOT / "config", ROOT / "forex_runtime/config", ROOT / "crypto_runtime/config")


def _json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _crypto_manifests() -> list[tuple[Path, dict]]:
    manifests: list[tuple[Path, dict]] = []
    for path in sorted((ROOT / "atoms_crypto").glob("*/*/manifest.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        assert isinstance(data, dict), path
        manifests.append((path, data))
    return manifests


def test_release_contract_matches_measured_architecture() -> None:
    release = _json(ROOT / "config/unified_release.json")
    assert release["core_version"] == "1.31.0"
    assert release["forex_atoms"] == 233
    assert release["crypto_atoms_packaged"] == 80
    assert release["full_crypto_source_atoms"] == 69
    assert release["additional_crypto_atoms"] == 9
    assert release["crypto_startup_authority"] == "manifest.startup_mode"
    assert release["crypto_auto_start_atoms"] == 33
    assert release["crypto_manual_atoms"] == 47
    assert release["manual_storage_atoms"] == [2702, 2707, 2720]
    assert release["official_launch_mode"] == "separate_market_buttons"
    assert release["dashboard_mode"] == "separate_market_dashboards"
    assert release["crypto_data_root"] == "crypto_runtime/var"


def test_release_and_layout_runtime_mirrors_are_exact() -> None:
    for filename in ("unified_release.json", "unified_layout.json"):
        canonical = (ROOT / "config" / filename).read_bytes()
        for config_dir in CONFIG_MIRRORS[1:]:
            assert (config_dir / filename).read_bytes() == canonical


def test_crypto_manifest_startup_mode_is_the_only_authority() -> None:
    manifests = _crypto_manifests()
    assert len(manifests) == 80
    counts = Counter(str(data.get("startup_mode")) for _, data in manifests)
    assert counts == Counter({"manual": 47, "auto": 33})

    modes = {int(data["id"]): data["startup_mode"] for _, data in manifests}
    for atom_id in (2702, 2707, 2720):
        assert modes[atom_id] == "manual"

    for config_dir in CONFIG_MIRRORS:
        layout = _json(config_dir / "unified_layout.json")
        assert "excluded_from_auto_start" not in layout
        assert len(layout["full_crypto_source_ids"]) == 69


def test_layout_ids_match_packaged_crypto_manifests() -> None:
    layout = _json(ROOT / "config/unified_layout.json")
    manifest_ids = {int(data["id"]) for _, data in _crypto_manifests()}
    declared_ids = (
        set(layout["phase_a_ids"])
        | {int(value) for value in layout["full_crypto_source_ids"].values()}
        | set(layout["additional_crypto_ids"])
    )
    assert declared_ids == manifest_ids
    assert len(declared_ids) == 80


def test_official_buttons_use_independent_market_launcher() -> None:
    release = _json(ROOT / "config/unified_release.json")
    launchers = release["official_launchers"]
    assert set(launchers) == {"forex", "crypto"}
    for market, relative in launchers.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert f"scripts\\launch_market.py --market {market}" in source
        assert "--both" not in source
        assert "launch_unified.py" not in source
        assert "governance\\app.py" not in source


def test_active_runtime_code_mirrors_match_canonical_sources() -> None:
    for relative in (
        "config/core_crypto.yaml",
        "governance/server.py",
        "governance/control_adapter.py",
        "governance/checks/check_events.py",
        "governance/ui/src/sections/Mexc.tsx",
        "scripts/run_crypto.py",
        "scripts/run_governance.py",
    ):
        canonical = (ROOT / relative).read_bytes()
        for runtime in ("forex_runtime", "crypto_runtime"):
            assert (ROOT / runtime / relative).read_bytes() == canonical


def test_crypto_2275_manual_result_contract_is_mirrored() -> None:
    relative = Path("قسم 2251-2300") / "2275_محرك_المخاطر"
    for filename in ("atom.py", "manifest.yaml", "التاريخ.md", "الشرح.md"):
        assert (ROOT / "atoms_crypto" / relative / filename).read_bytes() == (
            ROOT / "crypto_runtime/atoms" / relative / filename).read_bytes()


def test_crypto_data_paths_stay_under_crypto_runtime_var() -> None:
    data_root = (ROOT / "crypto_runtime/var").resolve()
    core_cfg = yaml.safe_load((ROOT / "config/core_crypto.yaml").read_text(encoding="utf-8"))
    assert core_cfg["snapshot_root"] == "var/snapshots"
    assert core_cfg["journal"]["path"] == "var/journal.jsonl"
    for relative in (core_cfg["snapshot_root"], core_cfg["journal"]["path"]):
        assert (ROOT / "crypto_runtime" / relative).resolve().is_relative_to(data_root)

    runner = (ROOT / "scripts/run_crypto.py").read_text(encoding="utf-8")
    assert 'CRYPTO_RUNTIME = ROOT / "crypto_runtime"' in runner
    assert 'CRYPTO_DATA_ROOT = CRYPTO_RUNTIME / "var"' in runner
    assert 'ROOT / "var" / "crypto"' not in runner
    for filename in ("analysis_settings.db", "news.db", "bridge.db"):
        assert f'CRYPTO_DATA_ROOT / "{filename}"' in runner

    server = (ROOT / "governance/server.py").read_text(encoding="utf-8")
    assert 'RUNTIME_ROOT = ROOT.parent / ("crypto_runtime" if MARKET == "crypto" else "forex_runtime")' in server
    assert 'DATA_ROOT = RUNTIME_ROOT / "var"' in server
    assert 'ANALYSIS_SETTINGS_DB = DATA_ROOT / "analysis_settings.db"' in server
    assert 'TRADE_DB = DATA_ROOT / "bridge.db"' in server
    assert 'MEXC_KEYS_PATH = DATA_ROOT / "mexc_api.json"' in server
    assert 'MANUAL_TRADE_RESULTS_DB = DATA_ROOT / "governance" / "manual_trade_results.db"' in server
    assert 'DATA_ROOT / "universe_membership.json"' in server
    assert 'ROOT.parent / "var" / MARKET' not in server
    assert 'ROOT.parent / "var" / "mexc_api.json"' not in server
