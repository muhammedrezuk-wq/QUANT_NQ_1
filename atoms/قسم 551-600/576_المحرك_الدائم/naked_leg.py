"""Naked-leg tracking for the perpetual engine (576, v3.6.0, 2026-08-27).

A halt landing in the real gap between a pair's two legs used to leave the
first leg open (real, unhedged, stop-loss-less) while the second leg
silently returned on HALT_BLOCKED -- and the atom still reported the pair
OPENED, since _open() raised nothing for that path. This module tracks
exactly the missing leg (never the whole pair) and completes it once its
account is no longer halted. Same idiom as market_inputs.py: plain
functions taking the atom itself and mutating its state directly, not a
class the atom must hold callbacks for.
"""
from __future__ import annotations

from typing import Any


async def enter(atom: Any, key: str, *, account_id: str, broker: str, symbol: str,
                pair_id: str, missing_role: str, lot: float, price: float,
                risk_budget: float, authority: tuple[str, str]) -> None:
    """Record a leg left naked by a halt landing mid-pair, and announce it
    with its own status -- the caller must never report OPENED for this."""
    atom._naked[key] = {"account_id": account_id, "broker": broker, "symbol": symbol,
                        "pair_id": pair_id, "missing_role": missing_role, "lot": lot,
                        "price": price, "risk_budget": risk_budget, "authority": authority}
    open_role = "SELL" if missing_role == "BUY" else "BUY"
    await atom._emit_state(account_id, broker, symbol, "NAKED_LEG_HALT_BLOCKED", {
        "pair_id": pair_id, "open_role": open_role, "missing_role": missing_role,
        "lot": lot, "price": round(price, 6),
    })


def to_rows(naked: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for key, info in naked.items():
        authority = info.get("authority")
        if not (isinstance(authority, tuple) and len(authority) == 2):
            continue
        row = dict(info)
        row["key"] = key
        row["authority"] = [authority[0], authority[1]]
        rows.append(row)
    return rows


def load_rows(naked: dict[str, dict[str, Any]], rows: Any, number, key_sep: str) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = row.get("key")
        authority = row.get("authority")
        lot = number(row.get("lot"))
        price = number(row.get("price"))
        budget = number(row.get("risk_budget"))
        if not (isinstance(key, str) and key_sep in key
                and isinstance(authority, list) and len(authority) == 2
                and isinstance(authority[0], str) and isinstance(authority[1], str)
                and isinstance(row.get("account_id"), str) and isinstance(row.get("broker"), str)
                and isinstance(row.get("symbol"), str) and isinstance(row.get("pair_id"), str)
                and row.get("missing_role") in ("BUY", "SELL")
                and lot is not None and price is not None and budget is not None):
            continue
        naked[key] = {"account_id": row["account_id"], "broker": row["broker"],
                      "symbol": row["symbol"], "pair_id": row["pair_id"],
                      "missing_role": row["missing_role"], "lot": lot, "price": price,
                      "risk_budget": budget, "authority": (authority[0], authority[1])}


async def complete_ready(atom: Any) -> None:
    """Retries exactly the missing leg for every tracked entry whose account
    is no longer halted; never reopens the leg that already exists."""
    for key in list(atom._naked):
        info = atom._naked.get(key)
        if info is None:
            continue
        account_id = info["account_id"]
        if atom._halt.blocks(account_id):
            continue
        role = info["missing_role"]
        side = "SELL" if role == "SELL" else "BUY"
        opened = await atom._open(account_id, info["broker"], info["symbol"], side,
                                  info["lot"], info["price"], info["pair_id"], role,
                                  info["risk_budget"], info["authority"])
        if not opened:
            continue
        atom._naked.pop(key, None)
        atom._opened += 1
        await atom._emit_state(account_id, info["broker"], info["symbol"], "OPENED", {
            "lot": info["lot"], "price": round(info["price"], 6),
            "pair_id": info["pair_id"], "pair_status": "COMPLETED_AFTER_HALT",
            info["authority"][0]: info["authority"][1],
        })
