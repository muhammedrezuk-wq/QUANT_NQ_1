from __future__ import annotations

import math
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from engine_persistence import snapshot as save_engine, restore as restore_engine
from shared.financial_scope import financial_key, row_key, text

_BUDGET_TOLERANCE = 1.01

ATOM_VERSION = "3.6.0"
EVENT_ACTIVATE = "perpetual.asset.activate"
EVENT_DEACTIVATE = "perpetual.asset.deactivate"
EVENT_PULSE = "SYS_SECOND"
EVENT_DIAL = "dial.profile.state"
EVENT_SPECS = "market.symbol_specs"
EVENT_CANDLE = "market_data.candle_closed"
EVENT_TICK = "market.tick.validated"
EVENT_LEDGER = "risk.asset_ledger.state"
EVENT_POSITIONS = "platform.positions.state"
EVENT_ACCOUNT = "platform.account.state"
EVENT_REQUEST = "execution.order.requested"
EVENT_STATE = "perpetual.entry.state"
# v3.5.0 (2026-08-25): the producer of order requests must HEAR the owner's
# halt. Measured: 576 did not subscribe to emergency.halt at all -- it kept
# publishing requests while the system was halted, leaving the block to 552
# alone. Contract and mechanics live in halt_gate.py (sibling module).
from halt_gate import EVENT_HALT, EVENT_RESET, HaltGate

ACTION_OPEN = "OPEN"
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
PROTECTION_NEUTRAL_HEDGE = "NEUTRAL_HEDGE"

ST_OPENED = "OPENED"
ST_ALREADY = "ALREADY_ACTIVE"
ST_MISSING = "MISSING_INPUTS"
ST_LOT_SMALL = "LOT_TOO_SMALL"
ST_REJECTED = "REJECTED"
# v3.6.0 (2026-08-27): a halt landing between a pair's two legs used to leave
# the BUY leg open (real, stop-loss-less) while SELL silently returned on
# HALT_BLOCKED -- yet the caller still reported ST_OPENED, since _open()
# raised nothing for that path. Fixed in naked_leg.py (see _try_key below
# and _on_halt_reset) -- that module owns the "NAKED_LEG_HALT_BLOCKED"
# status so it never drifts from the code that actually emits it.
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_AUTHORITY = "NO_PARENT_AUTHORITY"
EVENT_REJECTED = "perpetual.entry.rejected"
FIELD_PARENT_DECISION = "parent_decision_id"
FIELD_OWNER_COMMAND = "owner_command_id"
FIELD_COMMAND_ID = "command_id"

# Unit 1 (owner direct order 2026-08-23): declared authority becomes VERIFIED
# authority. A parent_decision_id is only accepted when this atom has actually
# seen 467 publish decision.gate.passed for that decision id. Owner commands
# (owner_command_id/command_id from 901) pass unchanged -- the owner route is
# legitimate by design and is not touched here.
# Campaign 450-901 batch B: the window itself lives in gate_window.py.
from gate_window import GateWindow, publish_rejection
import market_inputs
import naked_leg

EVENT_GATE_PASSED = "decision.gate.passed"
REASON_UNVERIFIED_DECISION = "DECISION_NOT_IN_GATE_WINDOW"

KEY_SEP = "|"
_SCOPE_PARTS = 3

def _scope(account: Any, broker: Any, symbol: Any) -> str:
    return KEY_SEP.join((text(account), text(broker), text(symbol)))

def _parts(key: str) -> tuple[str, str, str]:
    parts = key.split(KEY_SEP, 2)
    return (parts[0], parts[1], parts[2]) if len(parts) == _SCOPE_PARTS else ("", "", "")

PRICE_DP = 6
LOT_DP = 2


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _authority(payload: dict[str, Any]) -> tuple[str, str] | None:
    parent = text(payload.get(FIELD_PARENT_DECISION))
    if parent:
        return FIELD_PARENT_DECISION, parent
    owner = text(payload.get(FIELD_OWNER_COMMAND)) or text(payload.get(FIELD_COMMAND_ID))
    if owner:
        return FIELD_OWNER_COMMAND, owner
    return None


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._lot_step = 0.01
        self._min_lot = 0.01
        self._max_lot = 20.0
        self._fallback_stop_frac = 0.005
        self._broker_by_account: dict[str, str] = {}
        self._pending_specs: list[dict[str, Any]] = []
        self._vpu: dict[str, float] = {}
        self._price: dict[str, float] = {}
        self._stopfrac: dict[str, float] = {}
        self._budget: dict[str, float] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._actual_active: set[str] = set()
        self._epoch = 0
        self._restore_grade = "UNKNOWN"
        self._restore_reason = "NO_SNAPSHOT"
        self._active: set[str] = set()
        self._counter = 0
        self._opened = 0
        self._no_authority = 0
        self._gate = GateWindow()
        self._unverified_decisions = 0
        self._halt = HaltGate()
        self._naked: dict[str, dict[str, Any]] = {}

    async def _on_account(self, payload: dict[str, Any]) -> None:
        await market_inputs.on_account(self, payload)

    async def _on_dial(self, payload: dict[str, Any]) -> None:
        await market_inputs.on_dial(self, payload)

    async def _on_specs(self, payload: dict[str, Any]) -> None:
        await market_inputs.on_specs(self, payload)

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._lot_step = float(cfg.get("lot_step", 0.01))
        self._min_lot = float(cfg.get("min_lot", 0.01))
        self._max_lot = float(cfg.get("max_lot", 20.0))
        self._fallback_stop_frac = float(cfg.get("fallback_stop_distance_frac", 0.005))
        context.subscribe(EVENT_DIAL, self._on_dial)
        context.subscribe(EVENT_SPECS, self._on_specs)
        context.subscribe(EVENT_CANDLE, self._on_candle)
        context.subscribe(EVENT_TICK, self._on_tick)
        context.subscribe(EVENT_LEDGER, self._on_ledger)
        context.subscribe(EVENT_POSITIONS, self._on_positions)
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        context.subscribe(EVENT_ACTIVATE, self._on_activate)
        context.subscribe(EVENT_DEACTIVATE, self._on_deactivate)
        context.subscribe(EVENT_GATE_PASSED, self._on_gate_passed)
        context.subscribe(EVENT_PULSE, self._on_pulse)
        context.subscribe(EVENT_HALT, self._on_halt)
        context.subscribe(EVENT_RESET, self._on_halt_reset)

    async def _on_halt(self, payload: dict[str, Any]) -> None:
        self._halt.on_halt(payload)

    async def _on_halt_reset(self, payload: dict[str, Any]) -> None:
        self._halt.on_reset(payload)
        # v3.6.0: complete any leg left naked by a halt that landed mid-pair,
        # the moment its account is no longer blocked (naked_leg.py).
        await naked_leg.complete_ready(self)

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if self._epoch or not isinstance(payload, dict):
            return
        try:
            self._epoch = int(float(payload["official_time"]))
        except (KeyError, TypeError, ValueError):
            return

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        """Entry price from the validated tick, for every account on the same
        broker and symbol (owner stamp 2026-08-21, option A).

        Measured: this engine read the price from the CLOSED CANDLE only, and
        validated ticks exist solely for the DATA account (cTrader) because
        the feed router suppresses the secondary provider while the preferred
        one is alive -- by design, to stop a duplicated price. So the
        EXECUTION account (MT5) never saw a price at all and an activated
        asset sat on MISSING_INPUTS forever: not late, impossible. A price
        belongs to the instrument, not to the account carrying the feed.
        """
        if not self._running or not isinstance(payload, dict):
            return
        price = _number(payload.get("price"))
        if price is None:
            bid, ask = _number(payload.get("bid")), _number(payload.get("ask"))
            if bid is not None and ask is not None and bid > 0 and ask >= bid:
                price = (bid + ask) / 2.0
        scoped = financial_key(payload, str(payload.get("symbol") or ""),
                               self._broker_by_account)
        if scoped is None or price is None or price <= 0:
            return
        account, broker, sym = scoped
        for owner in {account, *self._broker_by_account}:
            if owner != account and self._broker_by_account.get(owner) != broker:
                continue
            key = _scope(owner, broker, sym)
            if self._price.get(key) != price:
                self._price[key] = price
                await self._retry_key(key)

    async def _on_candle(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        close = _number(payload.get("close")); scoped = financial_key(payload, symbol, self._broker_by_account)
        if scoped is not None and close is not None and close > 0:
            key = _scope(*scoped); self._price[key] = close
            await self._retry_key(key)

    async def _on_ledger(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        rows = payload.get("ledgers")
        for led in rows if isinstance(rows, list) else []:
            if not isinstance(led, dict):
                continue
            symbol = str(led.get("symbol") or "")
            budget = _number(led.get("budget", led.get("risk_budget", led.get("R"))))
            if symbol and budget is not None and budget > 0:
                scoped = financial_key(led, symbol, self._broker_by_account)
                if scoped is None: continue
                key = _scope(*scoped)
                self._budget[key] = budget
                await self._retry_key(key)

    async def _on_positions(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        seen: set[str] = set()
        rows = payload.get("positions")
        for pos in rows if isinstance(rows, list) else []:
            if not isinstance(pos, dict) or not pos.get("symbol"):
                continue
            account = text(pos.get("account_id") or payload.get("account_id")); broker = text(pos.get("broker") or payload.get("broker")) or self._broker_by_account.get(account, "")
            if account and broker: seen.add(_scope(account, broker, pos.get("symbol")))
        self._actual_active = seen
        self._active.update(seen)

    async def _on_deactivate(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if symbol:
            account = text(payload.get("account_id")); broker = text(payload.get("broker")) or self._broker_by_account.get(account, "")
            key = _scope(account, broker, symbol)
            self._active.discard(key)
            self._actual_active.discard(key)
            self._pending.pop(key, None)

    def _size(self, key: str, budget: float) -> tuple[float, float, float] | None:
        price = self._price.get(key)
        vpu = self._vpu.get(key)
        stop_frac = self._stopfrac.get(key, self._fallback_stop_frac)
        if price is None or vpu is None or stop_frac <= 0:
            return None
        stop_distance = price * stop_frac
        denom = stop_distance * vpu
        if denom <= 0:
            return None
        raw = budget / denom
        stepped = round(raw / self._lot_step) * self._lot_step
        if stepped * denom > budget * _BUDGET_TOLERANCE:
            stepped = math.floor(raw / self._lot_step) * self._lot_step
        lot = min(self._max_lot, max(0.0, stepped))
        return round(lot, LOT_DP), price, stop_distance

    async def _on_activate(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = text(payload.get("symbol")); account_id = text(payload.get("account_id"))
        broker = text(payload.get("broker")) or self._broker_by_account.get(account_id, "")
        if not symbol or not account_id or not broker:
            return
        authority = _authority(payload)
        if authority is None:
            self._no_authority += 1
            await publish_rejection(self._context, EVENT_REJECTED, account_id, broker, symbol, payload, REASON_NO_AUTHORITY)
            return
        if not self._gate_verified(authority):
            self._unverified_decisions += 1
            await publish_rejection(self._context, EVENT_REJECTED, account_id, broker, symbol, payload, REASON_UNVERIFIED_DECISION, {"parent_decision_id": authority[1]})
            return
        key = _scope(account_id, broker, symbol)
        if key in self._active or key in self._actual_active:
            self._active.add(key)
            self._pending.pop(key, None)
            await self._emit_state(account_id, broker, symbol, ST_ALREADY, {"actual_positions": True})
            return
        request = dict(payload)
        request[authority[0]] = authority[1]
        self._pending[key] = request
        await self._try_key(key, announce_missing=True)

    async def _on_gate_passed(self, payload: dict[str, Any]) -> None:
        self._gate.observe(payload)

    def _gate_verified(self, authority: tuple[str, str]) -> bool:
        if authority[0] != FIELD_PARENT_DECISION:
            return True
        return self._gate.has(authority[1])

    async def _retry_key(self, key: str) -> None:
        if key in self._pending and key not in self._active:
            await self._try_key(key, announce_missing=False)

    async def _try_key(self, key: str, announce_missing: bool) -> None:
        payload = self._pending.get(key)
        if payload is None or self._context is None:
            return
        account_id, broker, symbol = _parts(key)
        authority = _authority(payload)
        if authority is None:
            self._pending.pop(key, None)
            self._no_authority += 1
            await publish_rejection(self._context, EVENT_REJECTED, account_id, broker, symbol, payload, REASON_NO_AUTHORITY)
            return
        if not self._gate_verified(authority):
            self._pending.pop(key, None)
            self._unverified_decisions += 1
            await publish_rejection(self._context, EVENT_REJECTED, account_id, broker, symbol, payload, REASON_UNVERIFIED_DECISION, {"parent_decision_id": authority[1]})
            return
        budget = _number(payload.get("budget", payload.get("risk_budget")))
        if budget is None:
            budget = self._budget.get(key)
        if budget is None or budget <= 0:
            if announce_missing:
                await self._emit_state(account_id, broker, symbol, ST_MISSING, {"budget": budget})
            return
        sized = self._size(key, budget)
        if sized is None:
            if announce_missing:
                await self._emit_state(account_id, broker, symbol, ST_MISSING, {
                    "price": self._price.get(key), "vpu": self._vpu.get(key),
                    "stop_frac": self._stopfrac.get(key, self._fallback_stop_frac),
                })
            return
        lot, price, stop_distance = sized
        if lot < self._min_lot:
            self._pending.pop(key, None)
            await self._emit_state(account_id, broker, symbol, ST_LOT_SMALL,
                                    {"lot": lot, "budget": budget})
            return
        self._pending.pop(key, None)
        self._active.add(key)
        self._counter += 1
        pair_id = "pair-%s-%s-%s-%d-%d" % (account_id, broker, symbol, self._epoch,
                                        self._counter)
        try:
            buy_ok = await self._open(account_id, broker, symbol, SIDE_BUY, lot, price, pair_id, "BUY", budget / 2.0, authority)
            if not buy_ok:
                # Halted before either leg opened -- nothing naked yet, same
                # retry-later behaviour as before this fix.
                self._active.discard(key)
                self._pending[key] = dict(payload)
                return
            sell_ok = await self._open(account_id, broker, symbol, SIDE_SELL, lot, price, pair_id, "SELL", budget / 2.0, authority)
        except Exception:
            self._active.discard(key)
            self._pending[key] = dict(payload)
            raise
        if not sell_ok:
            # v3.6.0 (naked-leg fix, see naked_leg.py): BUY already left this
            # atom as a real order -- retrying the pair from scratch would
            # risk a second BUY once the halt clears. `key` stays in _active
            # so a fresh activate reads ALREADY_ACTIVE, not a second pair.
            await naked_leg.enter(self, key, account_id=account_id, broker=broker, symbol=symbol,
                                  pair_id=pair_id, missing_role="SELL", lot=lot, price=price,
                                  risk_budget=budget / 2.0, authority=authority)
            return
        self._opened += 1
        await self._emit_state(account_id, broker, symbol, ST_OPENED, {
            "lot": lot, "price": round(price, PRICE_DP),
            "stop_distance": round(stop_distance, PRICE_DP),
            "pair_id": pair_id, "pair_status": "REQUESTED",
            authority[0]: authority[1],
        })

    async def _open(self, account_id: str, broker: str, symbol: str, side: str, lot: float,
                    price: float, pair_id: str, role: str, risk_budget: float,
                    authority: tuple[str, str]) -> bool:
        if self._context is None:
            return False
        if self._halt.blocks(account_id):
            # The halt blocks NEW exposure at its source -- not only at 552.
            await self._emit_state(account_id, broker, symbol, "HALT_BLOCKED",
                                   {"pair_id": pair_id, "leg_role": role})
            return False
        request_id = "%s-%s-a1" % (pair_id, role.lower())
        await self._context.publish(EVENT_REQUEST, {
            "request_id": request_id, "account_id": account_id, "broker": broker,
            "action": ACTION_OPEN, "symbol": symbol, "side": side,
            "volume": lot, "reference_price": round(price, PRICE_DP),
            "stop_loss": None, "take_profit": None, "origin": "perpetual",
            "pair_id": pair_id, "leg_role": role, "attempt": 1,
            "pair_required": True, "pair_volume": lot,
            "protection_mode": PROTECTION_NEUTRAL_HEDGE,
            "purpose": "INITIAL_NEUTRAL", "risk_budget": risk_budget,
            authority[0]: authority[1],
        })
        return True

    async def _emit_state(self, account_id: str, broker: str, symbol: str, status: str,
                          extra: dict[str, Any]) -> None:
        if self._context is None:
            return
        body = {"account_id": account_id, "broker": broker, "symbol": symbol, "status": status}
        body.update(extra)
        await self._context.publish(EVENT_STATE, body)

    async def snapshot(self) -> dict[str, Any]:
        return save_engine(self, ATOM_VERSION)

    async def restore(self, state: dict[str, Any]) -> None:
        restore_engine(self, state, _number, KEY_SEP)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        # v3.6.0: a naked leg must never be invisible -- in the one-line
        # message (shows up in any log/grep of health alone) and DEGRADED
        # so a dashboard/alert can't miss it, not buried in details only.
        details = {"active": len(self._active), "actual_active": len(self._actual_active),
                  "pending": len(self._pending), "opened": self._opened, "naked_legs": len(self._naked),
                  "rejected_no_authority": self._no_authority, "rejected_unverified_decision": self._unverified_decisions,
                  "gate_window": len(self._gate.decisions), "prices": len(self._price), "specs": len(self._vpu)}
        return HealthStatus(state=HealthState.DEGRADED if self._naked else HealthState.HEALTHY,
            message="active=%d pending=%d opened=%d naked=%d rejected_no_authority=%d rejected_unverified_decision=%d" % (
                len(self._active), len(self._pending), self._opened, len(self._naked),
                self._no_authority, self._unverified_decisions), details=details)
