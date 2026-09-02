from __future__ import annotations

from typing import Any


def text(value: Any) -> str:
    return str(value or "").strip()


def account_broker(payload: dict[str, Any], known: dict[str, str]) -> tuple[str, str] | None:
    account = text(payload.get("account_id"))
    if not account:
        return None
    broker = text(payload.get("broker")) or known.get(account, "")
    if not broker:
        return None
    return account, broker


def financial_key(payload: dict[str, Any], symbol: Any,
                  known: dict[str, str]) -> tuple[str, str, str] | None:
    owner = account_broker(payload, known)
    sym = text(symbol)
    if owner is None or not sym:
        return None
    return owner[0], owner[1], sym


def row_key(payload: dict[str, Any], row: dict[str, Any],
            known: dict[str, str]) -> tuple[str, str, str] | None:
    merged = dict(payload)
    merged.update({k: v for k, v in row.items() if v not in (None, "")})
    return financial_key(merged, row.get("asset_canonical") or row.get("symbol"), known)


def key_text(account: Any, broker: Any, symbol: Any, sep: str = "|") -> str:
    return sep.join((text(account), text(broker), text(symbol)))
