from __future__ import annotations

from typing import Any


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


async def update_legs(atom: Any, payload: dict[str, Any]) -> None:
    if not atom._running or not isinstance(payload, dict):
        return
    positions = payload.get("positions")
    if not isinstance(positions, list):
        return
    legs: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("symbol") or "")
        ticket = str(position.get("ticket") or "").strip()
        account = str(position.get("account_id") or payload.get("account_id") or "")
        if not account or not symbol or not ticket:
            continue
        legs.setdefault(account + "|" + symbol, []).append({
            "ticket": ticket, "side": str(position.get("side") or ""),
            "volume": number(position.get("volume")) or 0.0,
            "profit": number(position.get("profit")) or 0.0})
    atom._legs = legs
