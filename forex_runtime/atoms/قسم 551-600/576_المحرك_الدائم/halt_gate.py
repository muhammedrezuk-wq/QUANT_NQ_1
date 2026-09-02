"""Emergency-halt gate for the perpetual engine (v3.5.0, nq seal 2026-08-25).

The producer of order requests must HEAR the owner's halt. Same contract as
552/575: account_id halts one account, scope=SYSTEM halts all, and a halt
carrying neither is counted and never widened (silent widening is a lie in
the other direction). The halt blocks NEW exposure only -- reductions and
management stay free on their own paths.
"""
from __future__ import annotations

from typing import Any

EVENT_HALT = "emergency.halt"
EVENT_RESET = "risk.kill_switch.reset_requested"


class HaltGate:
    __slots__ = ("global_halted", "halted_accounts", "blocked_count",
                 "identity_blocked")

    def __init__(self) -> None:
        self.global_halted = False
        self.halted_accounts: set[str] = set()
        self.blocked_count = 0
        self.identity_blocked = 0

    def on_halt(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        scope = str(payload.get("scope") or "").strip().upper()
        if scope == "SYSTEM":
            self.global_halted = True
        elif account:
            self.halted_accounts.add(account)
        else:
            # A halt with no identity is counted, never widened.
            self.identity_blocked += 1

    def on_reset(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        scope = str(payload.get("scope") or "").strip().upper()
        if scope == "SYSTEM":
            self.global_halted = False
            self.halted_accounts.clear()
        elif account:
            self.halted_accounts.discard(account)

    def blocks(self, account_id: str) -> bool:
        blocked = self.global_halted or account_id in self.halted_accounts
        if blocked:
            self.blocked_count += 1
        return blocked
