"""Deployment-network preflight for externally previewable services.

The public Core APIs use the existing QUANT_NQ secret vault. This module
loads only the API credential needed for the process, never writes the secret
into the repository, and keeps it in the child process environment.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PyYAML is required for network preflight") from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_API_SECRET_NAMES = (
    "api_key",
    "core_api_key",
    "gov_api_key",
    "quant_core_api_key",
    "quant_gov_api_key",
)


def _config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"invalid core config: {path}")
    return data


def _runtime_base(market: str) -> Path:
    return PROJECT_ROOT / ("forex_runtime" if market == "forex" else "crypto_runtime")


def _resolve_runtime_path(raw: str | Path, market: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (_runtime_base(market) / path).resolve()


def _load_api_key_from_vault(market: str, cfg: dict[str, Any]) -> str | None:
    """Read the API key from the existing encrypted vault without logging it."""
    secrets = cfg.get("secrets") or {}
    if not bool(secrets.get("enabled", True)):
        return None

    # Keep the exact runtime default used by run_core.py. Market configs may
    # override this path when their runtime layout is different.
    vault_raw = secrets.get("vault_path", "runtime/secrets.enc")
    vault_path = _resolve_runtime_path(vault_raw, market)
    if not vault_path.exists():
        return None

    try:
        from security import FileSecretProvider
    except ImportError:
        return None

    dpapi_raw = secrets.get("dpapi_blob")
    dpapi_path = _resolve_runtime_path(dpapi_raw, market) if dpapi_raw else None
    provider = FileSecretProvider(
        vault_path,
        dpapi_blob=dpapi_path,
        allow_prompt=False,
        auto_open=True,
    )
    try:
        for name in _API_SECRET_NAMES:
            value = provider.get_secret(name)
            if value is not None and str(value).strip():
                return str(value).strip()
    finally:
        provider.clear()
    return None


def ensure_api_credential(market: str, cfg: dict[str, Any]) -> str:
    """Ensure the API credential is available to the running process.

    Explicit environment configuration remains supported for managed
    deployments; otherwise the existing encrypted vault is the source of
    truth. The returned credential is never printed.
    """
    existing = (
        os.environ.get("QUANT_CORE_API_KEY")
        or os.environ.get("QUANT_GOV_API_KEY")
    )
    if existing and existing.strip():
        return existing.strip()

    value = _load_api_key_from_vault(market, cfg)
    if value:
        os.environ["QUANT_CORE_API_KEY"] = value
        return value

    raise RuntimeError(
        f"{market}: public API binding requires an API key from the configured "
        "secret vault or QUANT_CORE_API_KEY/QUANT_GOV_API_KEY"
    )


def validate_config(path: Path, expected_port: int, market: str) -> dict[str, Any]:
    data = _config(path)
    api = data.get("api") or {}
    if not bool(api.get("enable_api", True)):
        raise RuntimeError(f"API disabled in {path}")
    host = str(api.get("host", "")).strip()
    port = int(api.get("port", -1))
    if host != "0.0.0.0":
        raise RuntimeError(
            f"{path}: public preview requires api.host=0.0.0.0; found {host!r}"
        )
    if port != expected_port:
        raise RuntimeError(
            f"{path}: expected api.port={expected_port}; found {port}"
        )
    # الوضع المحلي يفتح لوحة التشخيص والنواة على الجهاز فقط. لا نمنع الإقلاع
    # بسبب غياب سر خارجي؛ الاتصال الأونلاين يبقى مقفولًا وتظهر حالته في اللوحة.
    if os.environ.get("QUANT_LOCAL_MODE", "").strip() != "1":
        ensure_api_credential(market, data)
    return {"host": host, "port": port}


def validate_market(market: str) -> dict[str, Any]:
    market = str(market).strip().lower()
    if market == "forex":
        return validate_config(PROJECT_ROOT / "config" / "core_forex.yaml", 8010, market)
    if market == "crypto":
        return validate_config(PROJECT_ROOT / "config" / "core_crypto.yaml", 8020, market)
    raise ValueError(f"unknown market: {market}")


def validate_all() -> dict[str, dict[str, Any]]:
    return {
        "forex": validate_market("forex"),
        "crypto": validate_market("crypto"),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate QUANT_NQ network exposure before startup")
    parser.add_argument("--market", choices=("forex", "crypto", "all"), default="all")
    args = parser.parse_args()

    try:
        result = validate_all() if args.market == "all" else {args.market: validate_market(args.market)}
    except Exception as exc:  # noqa: BLE001
        print(f"NETWORK PREFLIGHT: FAIL — {exc}")
        return 2

    for market, cfg in result.items():
        print(
            f"NETWORK PREFLIGHT: PASS — {market} API {cfg['host']}:{cfg['port']} "
            "authentication=vault-or-environment"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
