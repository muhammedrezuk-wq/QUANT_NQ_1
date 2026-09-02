from __future__ import annotations

from typing import Any

from shared.financial_scope import row_key, text


def _scope(account, broker, symbol):
    return "|".join((text(account), text(broker), text(symbol)))


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


async def on_account(atom, payload: dict[str, Any]) -> None:
    if not atom._running or not isinstance(payload, dict):
        return
    account = text(payload.get("account_id")); broker = text(payload.get("broker"))
    if account and broker:
        atom._broker_by_account[account] = broker
        pending, atom._pending_specs = atom._pending_specs, []
        for item in pending: await on_specs(atom, item)


async def on_dial(atom, payload: dict[str, Any]) -> None:
    if not atom._running or not isinstance(payload, dict):
        return
    rows = payload.get("profiles")
    for prof in rows if isinstance(rows, list) else []:
        if not isinstance(prof, dict):
            continue
        symbol = str(prof.get("symbol") or "")
        frac = _number(prof.get("stop_distance_frac"))
        if symbol and frac is not None:
            account = text(prof.get("account_id")); broker = text(prof.get("broker")) or atom._broker_by_account.get(account, "")
            key = _scope(account, broker, symbol)
            if not account or not broker: continue
            atom._stopfrac[key] = frac
            await atom._retry_key(key)


async def on_specs(atom, payload: dict[str, Any]) -> None:
    if not atom._running or not isinstance(payload, dict):
        return
    rows = payload.get("symbols", [])
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        tick_value = _number(row.get("tick_value")); tick_size = _number(row.get("tick_size"))
        scoped = row_key(payload, row, atom._broker_by_account)
        if scoped is not None and tick_value is not None and tick_size and tick_size > 0:
            vpu = tick_value / tick_size
            if vpu > 0:
                key = _scope(*scoped); atom._vpu[key] = vpu
                await atom._retry_key(key)
        elif text(row.get("account_id") or payload.get("account_id")) and payload not in atom._pending_specs:
            atom._pending_specs.append(dict(payload))
