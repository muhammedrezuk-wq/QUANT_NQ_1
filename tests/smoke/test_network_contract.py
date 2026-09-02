from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: str) -> dict:
    data = yaml.safe_load((ROOT / path).read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict), path
    return data


def test_forex_api_binding_contract() -> None:
    cfg = load_yaml("config/core_forex.yaml")
    assert cfg["api"]["enable_api"] is True
    assert cfg["api"]["host"] == "0.0.0.0"
    assert cfg["api"]["port"] == 8010
    assert cfg["secrets"]["enabled"] is True
    assert cfg["secrets"]["allow_prompt"] is False
    assert cfg["secrets"]["vault_path"] == "../runtime/secrets.enc"


def test_crypto_api_binding_contract() -> None:
    cfg = load_yaml("config/core_crypto.yaml")
    assert cfg["api"]["enable_api"] is True
    assert cfg["api"]["host"] == "0.0.0.0"
    assert cfg["api"]["port"] == 8020
    assert cfg["secrets"]["enabled"] is True
    assert cfg["secrets"]["allow_prompt"] is False
    assert cfg["secrets"]["dpapi_blob"] == "runtime/crypto.key"


def test_generic_core_stays_local() -> None:
    cfg = load_yaml("config/core.yaml")
    assert cfg["api"]["host"] == "127.0.0.1"
    assert cfg["api"]["port"] == 8010


def test_public_launchers_run_network_preflight() -> None:
    for path, market in (("scripts/run_forex.py", "forex"), ("scripts/run_crypto.py", "crypto")):
        source = (ROOT / path).read_text(encoding="utf-8")
        assert "validate_market" in source, path
        assert f'validate_market("{market}")' in source, path


def test_official_launch_buttons_are_market_specific() -> None:
    buttons = {
        "أزرار التشغيل/تشغيل الفوركس الموحد.bat": "forex",
        "أزرار التشغيل/تشغيل الكريبتو الموحد.bat": "crypto",
    }
    for path, market in buttons.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        assert f"scripts\\launch_market.py --market {market}" in text
        assert "--both" not in text
        assert "launch_unified.py" not in text
        assert "governance\\app.py" not in text


def test_hub_external_default_and_internal_backend_isolation() -> None:
    text = (ROOT / "governance/unified_hub.py").read_text(encoding="utf-8")
    assert 'os.environ.get("QUANT_HUB_HOST", "0.0.0.0")' in text
    assert 'BACKENDS = {"forex": ("127.0.0.1", 8092), "crypto": ("127.0.0.1", 8093)}' in text


def test_preflight_fails_without_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    from governance import network_preflight as preflight

    monkeypatch.delenv("QUANT_CORE_API_KEY", raising=False)
    monkeypatch.delenv("QUANT_GOV_API_KEY", raising=False)
    monkeypatch.setattr(preflight, "_load_api_key_from_vault", lambda market, cfg: None)
    with pytest.raises(RuntimeError, match="requires an API key"):
        preflight.ensure_api_credential("forex", load_yaml("config/core_forex.yaml"))


def test_preflight_accepts_environment_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    from governance import network_preflight as preflight

    monkeypatch.setenv("QUANT_CORE_API_KEY", "smoke-test-key")
    assert preflight.ensure_api_credential("forex", load_yaml("config/core_forex.yaml")) == "smoke-test-key"
