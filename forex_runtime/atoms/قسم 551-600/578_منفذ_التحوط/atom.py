from __future__ import annotations

import asyncio
from typing import Any

from core.contracts.atom import AtomBase, AtomContext
from failure_view import DeltaFailures
import health_view
from flood_guard import DEFAULT_RESEND_HOLD_S, FloodGuard
from pair_events import PairEventMixin
from pair_memory import check_against_broker, seal, unseal
import pair_store
from request_identity import request_identity
from stop_support import STOP_FROM_FALLBACK, catastrophe_stop
from shared.financial_scope import text

ATOM_VERSION = "5.4.0"
# v5.3.0 (2026-08-25): pair memory is DURABLE (pair_store) -- every pair
# mutation is written through immediately and boot prefers the durable
# record over the clean-stop snapshot. Measured root: an unclean death
# erased the snapshot-only memory, the surviving broker leg graded as a
# stranger, and the whole path froze on SNAPSHOT_DISAGREES_WITH_BROKER.
# The owner's 2026-08-16 line is untouched: continuity from the store,
# truth from the broker picture.

EVENT_TARGET = "perpetual.target.state"
EVENT_SNAPSHOT = "execution.snapshot.state"
EVENT_REQUEST = "execution.order.requested"
EVENT_VALIDATED = "risk.validation.completed"
EVENT_REJECTED = "execution.order.rejected"
EVENT_WRITE_FAILED = "platform.brain_signal.write_failed"
EVENT_ACK = "execution.command.ack"
EVENT_CMD_FAILED = "execution.command.failed"
EVENT_TRADE = "platform.trade_event"
EVENT_QUALITY = "execution.quality.state"
EVENT_DIVERGENCE = "execution.reference_divergence.state"
EVENT_ACCOUNT = "platform.account.state"
EVENT_TERMINAL = "platform.terminal_state"
EVENT_CLOCK = "SYS_SECOND"
EVENT_PAIR_STATE = "perpetual.pair.state"
EVENT_POSITIONS = "platform.positions.state"
EVENT_ESCALATION = "perpetual.owner.escalation"
EVENT_ASSET_COMMAND = "risk.asset.command"

# Unit 1 (owner direct order 2026-08-23): declared decision identity becomes
# VERIFIED identity on the entry path. An OPEN/ADD whose payload CLAIMS a
# decision_id/gate_request_id is only emitted when 467 actually published
# decision.gate.passed for it.
# v5.2.0 (2026-08-25, layer-3 no-side-path contract): a payload carrying NO
# decision identity at all is now BLOCKED and counted (no_identity_entries)
# instead of passing -- measured: the snapshot lineage (583) carries zero
# decision identity, so the old "pass as before" made _gate_allows_entry
# return True ALWAYS, i.e. an exposure-increasing side path around the gate.
# Fail-closed until the 581->583 identity wiring lands; the counter makes the
# gap loud. CLOSE_PARTIAL (protection) is never gated here: reducing exposure
# must not depend on gate bookkeeping.
EVENT_GATE_PASSED = "decision.gate.passed"
GATE_WINDOW_MAX = 2048
# v5.2.0: the producer of order requests must HEAR the owner's halt (same
# contract as 552/575/576): account_id halts one account, scope=SYSTEM halts
# all, a halt with neither is counted and never widened. Halt blocks NEW
# exposure (OPEN/ADD) only -- reductions stay free.
EVENT_HALT = "emergency.halt"
EVENT_RESET = "risk.kill_switch.reset_requested"

ACTION_OPEN = "OPEN"
ACTION_CLOSE_PARTIAL = "CLOSE_PARTIAL"
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
PROTECTION_NEUTRAL_HEDGE = "NEUTRAL_HEDGE"
PROTECTION_PERPETUAL = "PERPETUAL_BUDGET"

STATUS_REQUESTED = "REQUESTED"
STATUS_RETRY = "RETRY_REQUESTED"
STATUS_ACTUAL = "ACTUAL"
STATUS_FAILED = "FAILED"
STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL = "PARTIAL"

SEP = "|"

# NQ seal, item 22, package T (T1): the decision identity thread. Every order
# request re-published on the execution chain carries the decision id and the
# gate request id exactly as they arrived on its input (the target/snapshot).
# A missing value passes as None and is DECLARED -- never invented -- in the
# same style the decision chain used tonight (467/466: identity_incomplete).
IDENTITY_FIELDS = ("decision_id", "gate_request_id")
WARNING_IDENTITY = "identity_incomplete"


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _key(account: Any, broker: Any, symbol: Any) -> str:
    return SEP.join((text(account), text(broker), text(symbol)))


class Atom(PairEventMixin, AtomBase):

    def __init__(self) -> None:
        self._context = None
        self._running = False
        self._lot_step = 0.01
        self._min_volume = 0.01
        self._reward_risk = 2.0
        self._max_attempts = 3
        self._flood_guard = FloodGuard(DEFAULT_RESEND_HOLD_S)
        self._official_time: float | None = None
        self._watermark: float | None = None
        self._counter = 0
        self._trade_allowed: dict[str, bool] = {}
        self._broker_by_account: dict[str, str] = {}
        self._pairs: dict[str, dict[str, Any]] = {}
        self._quality: dict[str, dict[str, Any]] = {}
        self._divergence = {}
        self._request_map: dict[str, tuple[str, str]] = {}
        self._catastrophe_multiple = 3.0
        self._fallback_stop_frac = 0.02
        self._fallback_stops = 0
        self._no_stop_skipped = 0
        self._no_identity_skipped = 0
        self._replay_skipped = 0
        self._identity_incomplete = 0
        self._delta_failures = DeltaFailures()
        self._seen = 0
        self._order_requests_emitted = 0
        self._entries_blocked = 0
        self._snapshot_blocked = 0
        self._gate_window: dict[str, str] = {}
        self._gate_blocked = 0
        self._no_identity_entries = 0
        self._halted_global = False
        self._halted_accounts: set[str] = set()
        self._halt_blocked = 0
        self._halt_identity_blocked = 0
        self._retries = 0
        self._actual = 0
        self._exhausted = 0
        self._restore_grade = "UNKNOWN"
        self._reconciled = False
        self._reconcile_reason = "NO_LIVE_PICTURE"
        self._conflict: dict[str, Any] = {}
        self._position_picture: dict[tuple[str, str], dict[str, bool]] = {}
        self._position_picture_blocked = 0
        self._pair_store_path = pair_store.DEFAULT_PATH
        self._pair_store_error = ""
        self._durable_pairs_loaded = False
        self._persist_lock = asyncio.Lock()

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._lot_step = float(cfg.get("lot_step", 0.01))
        self._min_volume = float(cfg.get("min_volume", 0.01))
        self._reward_risk = float(cfg.get("reward_risk", 2.0))
        self._max_attempts = max(1, int(cfg.get("max_attempts", 3)))
        self._catastrophe_multiple = max(1.0, float(cfg["catastrophe_stop_multiple"]))
        self._fallback_stop_frac = float(cfg["fallback_stop_frac"])
        self._flood_guard = FloodGuard(float(cfg.get("resend_hold_s", DEFAULT_RESEND_HOLD_S)))
        context.subscribe(EVENT_SNAPSHOT, self._on_target)
        context.subscribe(EVENT_REQUEST, self._on_requested)
        context.subscribe(EVENT_VALIDATED, self._on_validated)
        context.subscribe(EVENT_REJECTED, self._on_rejected)
        context.subscribe(EVENT_WRITE_FAILED, self._on_send_failure)
        context.subscribe(EVENT_ACK, self._on_ack)
        context.subscribe(EVENT_CMD_FAILED, self._on_send_failure)
        context.subscribe(EVENT_TRADE, self._on_trade)
        context.subscribe(EVENT_QUALITY, self._on_quality)
        context.subscribe(EVENT_DIVERGENCE, self._on_divergence)
        context.subscribe(EVENT_ACCOUNT, self._on_external)
        context.subscribe(EVENT_TERMINAL, self._on_external)
        context.subscribe(EVENT_CLOCK, self._on_external)
        context.subscribe(EVENT_POSITIONS, self._on_positions)
        context.subscribe(EVENT_GATE_PASSED, self._on_gate_passed)
        context.subscribe(EVENT_HALT, self._on_halt)
        context.subscribe(EVENT_RESET, self._on_halt_reset)
        # v5.3.0: durable memory loads first -- it precedes and outranks
        # the clean-stop snapshot.
        self._pair_store_path = str(cfg.get("pair_store_path",
                                            pair_store.DEFAULT_PATH))
        sealed = pair_store.load(self._pair_store_path)
        if sealed is not None:
            loaded = unseal(sealed)
            if loaded["pairs"] or loaded["counter"]:
                self._pairs.update(loaded["pairs"])
                if loaded["counter"] is not None:
                    self._counter = loaded["counter"]
                self._flood_guard.restore(loaded.get("flood_guard"))
                for pair_id, pair in self._pairs.items():
                    for role, leg in (pair.get("legs") or {}).items():
                        request_id = str((leg or {}).get("request_id") or "")
                        if request_id:
                            self._request_map[request_id] = (pair_id, role)
                self._durable_pairs_loaded = True
                self._restore_grade = "DURABLE"

    async def _persist_pairs(self) -> None:
        # Every pair transition calls this (blocking sqlite write, found
        # during the 304-atom audit): the lock makes writes commit in the
        # same order they were issued -- off the event loop thread, but a
        # slower-to-schedule older snapshot can never land on disk after a
        # newer one and silently roll the durable record backward.
        async with self._persist_lock:
            sealed = seal(ATOM_VERSION, self._counter, self._pairs, self._official_time, self._watermark, self._flood_guard.snapshot())
            self._pair_store_error = await asyncio.to_thread(pair_store.save, self._pair_store_path, sealed)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _round_volume(self, value: float) -> float: return round(round(value / self._lot_step) * self._lot_step, 8) if self._lot_step > 0 else round(value, 8)

    def _with_identity(self, body: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        """T1: carry decision_id/gate_request_id from the input payload.

        Available values pass through untouched
        an absent one passes as None
        and the request declares it (identity_incomplete) -- nothing is ever
        made up here."""
        missing = [field for field in IDENTITY_FIELDS if not source.get(field)]
        for field in IDENTITY_FIELDS:
            body[field] = source.get(field)
        if missing:
            self._identity_incomplete += 1
            body["identity_missing"] = missing
            body["identity_warnings"] = [WARNING_IDENTITY]
        return body

    async def snapshot(self) -> dict[str, Any]:
        return seal(ATOM_VERSION, self._counter, self._pairs, self._official_time,
                    self._watermark, self._flood_guard.snapshot())

    async def restore(self, state: dict[str, Any]) -> None:
        # v5.3.0: the durable record is always newer than the clean-stop
        # snapshot -- once loaded, the snapshot's pairs are ignored (they
        # may be older) and everything else stays as it was.
        if self._durable_pairs_loaded:
            return
        loaded = unseal(state)
        self._restore_grade = loaded["grade"]
        self._pairs.update(loaded["pairs"])
        if loaded["counter"] is not None:
            self._counter = loaded["counter"]
        self._request_map.clear()
        for pair_id, pair in self._pairs.items():
            for role, leg in (pair.get("legs") or {}).items():
                request_id = str((leg or {}).get("request_id") or "")
                if request_id:
                    self._request_map[request_id] = (pair_id, role)
        self._flood_guard.restore(loaded.get("flood_guard"))

    async def _on_positions(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account=text(payload.get("account_id"))
        broker=text(payload.get("broker")) or self._broker_by_account.get(account,"")
        if account and broker:
            self._position_picture[(account,broker)]={
                "new":payload.get("usable_for_new_exposure") is True,
                "protection":payload.get("usable_for_protection") is True}
        if payload.get("usable_for_protection") is not True:
            self._reconciled=False
            self._reconcile_reason="POSITION_PICTURE_NOT_USABLE"
            self._conflict={}
            return
        v = check_against_broker(self._pairs, payload.get("positions"))
        self._reconciled = v["reconciled"]
        self._reconcile_reason = v["reason"]
        self._conflict = v["conflict"]

    async def _on_quality(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account=text(payload.get("account_id"))
        broker=text(payload.get("broker")) or self._broker_by_account.get(account, "")
        symbol=text(payload.get("symbol"))
        if account and broker and symbol: self._quality[_key(account,broker,symbol)] = dict(payload)

    async def _on_divergence(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account=text(payload.get("account_id"))
        broker=text(payload.get("broker")) or self._broker_by_account.get(account, "")
        symbol=text(payload.get("symbol"))
        timeframe=text(payload.get("timeframe"))
        if not account or not broker or not symbol: return
        self._divergence[_key(account,broker,symbol)+SEP+timeframe]=dict(payload)

    async def _on_external(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        stamp = _float(payload.get("official_time"))
        if stamp is not None:
            self._official_time = stamp
            if self._watermark is None: self._watermark = stamp
        account = text(payload.get("account_id"))
        broker = text(payload.get("broker"))
        if account and broker: self._broker_by_account[account] = broker
        if account and isinstance(payload.get("trade_allowed"), (bool, int)):
            self._trade_allowed[account] = bool(payload["trade_allowed"])

    async def _on_target(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        if not self._official_time:
            self._replay_skipped += 1
            return
        produced = _float(payload.get("produced_at"))
        if produced is None or (self._watermark is not None and produced < self._watermark):
            self._replay_skipped += 1
            return
        self._seen += 1
        action = str(payload.get("action") or "HOLD").upper()
        status = str(payload.get("status") or "")
        symbol = text(payload.get("symbol"))
        account = text(payload.get("account_id"))
        broker = text(payload.get("broker")) or self._broker_by_account.get(account, "")
        if not symbol or not account or not broker or status != "READY" or action in ("HOLD", "BLOCKED"): return
        positive_leg = any((_float(payload.get(name)) or 0.0) > self._min_volume
                           for name in ("delta_buy", "delta_sell"))
        opening = positive_leg or ("delta_buy" not in payload and "delta_sell" not in payload
                                   and action not in ("REDUCE", "CLOSE", "CLOSE_PARTIAL"))
        if opening and payload.get("usable_for_new_exposure") is not True:
            self._snapshot_blocked += 1
            self._entries_blocked += 1
            return
        if not opening and payload.get("usable_for_protection") is not True:
            self._snapshot_blocked += 1
            return
        picture=self._position_picture.get((account,broker),{})
        if opening and picture.get("new") is not True:
            self._position_picture_blocked+=1
            self._entries_blocked+=1
            return
        if not opening and picture.get("protection") is not True:
            self._position_picture_blocked+=1
            return
        if not self._reconciled:
            self._entries_blocked += 1
            return
        if self._trade_allowed.get(account) is not True: return
        if self._protection_blocks(account, broker, symbol) and any((_float(payload.get(f)) or 0.0) > self._min_volume for f in ("delta_buy", "delta_sell")):
            self._entries_blocked += 1
            return
        if not self._flood_guard.allows(account, symbol, payload, self._official_time): return
        before = self._order_requests_emitted
        if "delta_buy" in payload or "delta_sell" in payload:
            await self._adjust_side(payload, account, broker, symbol, SIDE_BUY, "delta_buy")
            await self._adjust_side(payload, account, broker, symbol, SIDE_SELL, "delta_sell")
        else:
            delta = _float(payload.get("delta_net"))
            if delta is None or abs(delta) < self._min_volume: return
            await self._emit_open(payload, account, broker, symbol, SIDE_BUY if delta > 0 else SIDE_SELL, abs(delta), action)
        if self._order_requests_emitted > before:
            self._flood_guard.mark_sent(account, symbol, self._official_time)

    async def _adjust_side(self, payload: dict[str, Any], account: str, broker: str, symbol: str, side: str, field: str) -> None:
        delta = _float(payload.get(field)) or 0.0
        if abs(delta) < self._min_volume: return
        if delta > 0:
            await self._emit_open(payload, account, broker, symbol, side, delta, str(payload.get("action") or "ADD"))
            return
        remaining = self._round_volume(abs(delta))
        identity = request_identity(payload.get("snapshot_id"), self._official_time)
        if identity is None: self._no_identity_skipped += 1; return
        for leg in payload.get("current_legs", []) if isinstance(payload.get("current_legs"), list) else []:
            if remaining < self._min_volume or str(leg.get("side") or "").upper() != side: continue
            ticket = leg.get("ticket")
            leg_volume = _float(leg.get("volume")) or 0.0
            close_volume = self._round_volume(min(remaining, leg_volume))
            if not ticket or close_volume < self._min_volume: continue
            self._counter += 1
            await self._context.publish(EVENT_REQUEST, self._with_identity({"request_id": "reduce-%s-%s-%s-%s-%d" % (account, symbol, side.lower(), identity, self._counter), "account_id": account, "broker": broker, "action": ACTION_CLOSE_PARTIAL, "symbol": symbol, "side": side, "volume": close_volume, "ticket": ticket, "reference_price": _float(leg.get("current_price")) or payload.get("reference_price"), "stop_loss": None, "take_profit": None, "origin": "perpetual-delta", "purpose": "REDUCE", "target_net": payload.get("target_net"), "current_net": payload.get("current_net"), "delta_net": payload.get("delta_net")}, payload))
            self._order_requests_emitted += 1
            remaining = self._round_volume(remaining - close_volume)

    def _protection_blocks(self, account: str, broker: str, symbol: str, timeframe: str = "") -> bool:
        quality = self._quality.get(_key(account, broker, symbol))
        divergence = self._divergence.get(_key(account, broker, symbol) + SEP + timeframe)
        if divergence is None and timeframe:
            divergence = self._divergence.get(_key(account, broker, symbol) + SEP)
        return (quality is None or quality.get("status") not in ("READY", "HEALTHY")
                or divergence is None or divergence.get("status") != "SYNCED")

    async def _on_gate_passed(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict): return
        decision_id = text(payload.get("decision_id"))
        if not decision_id: return
        self._gate_window[decision_id] = text(payload.get("gate_request_id"))
        while len(self._gate_window) > GATE_WINDOW_MAX:
            self._gate_window.pop(next(iter(self._gate_window)))

    def _gate_allows_entry(self, payload: dict[str, Any]) -> bool:
        claimed = {field: text(payload.get(field)) for field in IDENTITY_FIELDS if payload.get(field)}
        if not claimed:
            # v5.2.0: no claimed identity = no verified parent = no NEW
            # exposure (fail-closed, counted). The old True here was the
            # measured always-open side path around the gate.
            self._no_identity_entries += 1
            return False
        known_requests = set(self._gate_window.values())
        return any((value in self._gate_window) or (value in known_requests) for value in claimed.values())

    async def _on_halt(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict): return
        account = text(payload.get("account_id"))
        scope = text(payload.get("scope")).upper()
        if scope == "SYSTEM": self._halted_global = True
        elif account: self._halted_accounts.add(account)
        else: self._halt_identity_blocked += 1

    async def _on_halt_reset(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict): return
        account = text(payload.get("account_id"))
        scope = text(payload.get("scope")).upper()
        if scope == "SYSTEM":
            self._halted_global = False
            self._halted_accounts.clear()
        elif account: self._halted_accounts.discard(account)

    async def _emit_open(self, payload: dict[str, Any], account: str, broker: str, symbol: str, side: str, volume: float, purpose: str) -> None:
        if self._halted_global or account in self._halted_accounts:
            self._halt_blocked += 1; return
        if not self._gate_allows_entry(payload): self._gate_blocked += 1; return
        if self._protection_blocks(account, broker, symbol, str(payload.get("timeframe") or "")): self._entries_blocked += 1; return
        price = _float(payload.get("reference_price"))
        volume = self._round_volume(volume)
        computed = catastrophe_stop(price, _float(payload.get("stop_distance_frac")), side,
                                    self._catastrophe_multiple, self._fallback_stop_frac)
        if computed is None or volume < self._min_volume:
            self._no_stop_skipped += 1
            return
        stop, working, distance, source = computed
        if source == STOP_FROM_FALLBACK: self._fallback_stops += 1
        identity = request_identity(payload.get("snapshot_id"), self._official_time)
        if identity is None: self._no_identity_skipped += 1; return
        self._counter += 1
        await self._context.publish(EVENT_REQUEST, self._with_identity({"request_id": "delta-%s-%s-%s-%s-%d" % (account, symbol, side.lower(), identity, self._counter), "account_id": account, "broker": broker, "action": ACTION_OPEN, "symbol": symbol, "side": side, "volume": volume, "reference_price": round(price, 8), "stop_loss": round(stop, 8), "take_profit": None, "protection_mode": PROTECTION_PERPETUAL, "origin": "perpetual-delta", "purpose": purpose, "target_net": payload.get("target_net"), "current_net": payload.get("current_net"), "delta_net": payload.get("delta_net"), "risk_budget": payload.get("risk_budget"), "asset_stop_distance": round(working, 8), "catastrophe_distance": round(distance, 8), "catastrophe_multiple": self._catastrophe_multiple, "stop_source": source, "stop_is_last_resort": True, "snapshot_id": payload.get("snapshot_id")}, payload))
        self._order_requests_emitted += 1

    async def health_check(self):
        return health_view.build(self)
