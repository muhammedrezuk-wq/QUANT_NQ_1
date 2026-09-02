from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.cycle_identity import cycle_key_of

ATOM_VERSION = "1.5.0"
# v1.5.0 (2026-08-27, item 12/27 of the 27-atom review -- an unknown
# command on a shared event silently splits the allow-list): _on_asset_
# command called self._allowed_by_account.setdefault(account, set(self.
# _allowed)) UNCONDITIONALLY whenever account was non-empty, before ever
# checking whether `command` was one it recognizes. risk.asset.command is
# a shared event -- any unrecognized command (a typo, a future command
# not meant for this atom, a malformed payload) or even a RECOGNIZED
# command with nothing to do (PAUSE on a symbol already absent, RESUME on
# one already present) still materialized a per-account snapshot of the
# global allow-list at that moment. Once materialized, _on_tick's lookup
# (self._allowed_by_account.get(account, self._allowed)) permanently
# prefers that frozen snapshot over the live global set -- a LATER global
# addition via _on_activate (no account_id) never reaches that account
# again. Silent, no log, no error. Fixed by computing the EFFECTIVE set
# read-only first and only materializing (setdefault) the per-account
# partition once a real mutation is about to happen.

EVENT_IN = "market.tick.validated"
EVENT_FILTER = "decision.filter.asset.state"
EVENT_WHITELIST = "allowed.symbols.state"
EVENT_ACTIVATE = "perpetual.asset.activate"
EVENT_ASSET_COMMAND = "risk.asset.command"

_REMOVE_COMMANDS = {"PAUSE", "FREEZE"}
_RESTORE_COMMANDS = {"RESUME", "UNFREEZE"}

ID_FILTER = "asset_filter"
STATUS_OK = "ok"
SIGNAL_ALLOW = "allow"
SIGNAL_BLOCK = "block"
METHOD = "whitelist"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_EMPTY = "EMPTY_WHITELIST_BLOCKS_ALL"

CONFIDENCE_PASS = 1.0
CONFIDENCE_FAIL = 0.0


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._allowed: set[str] = set()
        self._allowed_by_account: dict[str, set[str]] = {}
        self._seen = 0
        self._blocked = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._allowed = {str(s) for s in context.config.get("allowed_symbols", [])}
        context.subscribe(EVENT_IN, self._on_tick)
        context.subscribe(EVENT_ACTIVATE, self._on_activate)
        context.subscribe(EVENT_ASSET_COMMAND, self._on_asset_command)

    async def start(self) -> None:
        self._running = True
        await self._publish_whitelist()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _publish_whitelist(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_WHITELIST, {
            "id": ID_FILTER, "status": STATUS_OK, "allowed": sorted(self._allowed),
            "allowed_by_account": {a: sorted(v) for a, v in self._allowed_by_account.items()}})

    async def _on_activate(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").strip()
        account = str(payload.get("account_id") or "").strip()
        if not symbol:
            return
        if account:
            allowed = self._allowed_by_account.setdefault(account, set(self._allowed))
            allowed.add(symbol)
        else:
            self._allowed.add(symbol)
        # v1.x (nq seal 2026-08-25): an activation ALWAYS republishes the
        # whitelist -- even when the symbol was already listed. The old
        # "publish only on change" left a consumer that missed the boot-time
        # state with no second chance (measured live: 552 swallowed the boot
        # whitelist and the owner's activation republished nothing, so six
        # built orders died SYMBOL_NOT_ALLOWED). A cheap reaffirmation kills
        # that trap family.
        await self._publish_whitelist()

    async def _on_asset_command(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").strip()
        account = str(payload.get("account_id") or "").strip()
        command = str(payload.get("command") or "").strip().upper()
        if not symbol:
            return
        current = self._allowed_by_account.get(account, self._allowed) if account else self._allowed
        if command in _REMOVE_COMMANDS and symbol in current:
            allowed = self._allowed_by_account.setdefault(account, set(self._allowed)) if account else self._allowed
            allowed.discard(symbol)
            await self._publish_whitelist()
        elif command in _RESTORE_COMMANDS and symbol not in current:
            allowed = self._allowed_by_account.setdefault(account, set(self._allowed)) if account else self._allowed
            allowed.add(symbol)
            await self._publish_whitelist()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        timeframe = "tick"
        account = str(payload.get("account_id") or "")
        effective = self._allowed_by_account.get(account, self._allowed)
        passed = symbol in effective
        sequence = str(payload.get("sequence") or "")
        cycle_id = str(payload.get("cycle_id") or cycle_key_of(
            payload, symbol=symbol, timeframe=timeframe, period_start=sequence))
        self._seen += 1
        if not passed:
            self._blocked += 1
        await self._context.publish(EVENT_FILTER, {
            "symbol": symbol, "account_id": payload.get("account_id"),
            "id": ID_FILTER, "status": STATUS_OK,
            "cycle_id": cycle_id, "timeframe": timeframe,
            "signal": SIGNAL_ALLOW if passed else SIGNAL_BLOCK,
            "confidence": CONFIDENCE_PASS if passed else CONFIDENCE_FAIL,
            "warnings": [],
            "metadata": {"method": METHOD, "timeframe": timeframe, "passed": passed}})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"allowed_symbols": sorted(self._allowed), "seen": self._seen,
                   "blocked": self._blocked}
        if not self._allowed:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_EMPTY,
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="allowed=%d seen=%d blocked=%d" % (
                                len(self._allowed), self._seen, self._blocked),
                            details=details)
