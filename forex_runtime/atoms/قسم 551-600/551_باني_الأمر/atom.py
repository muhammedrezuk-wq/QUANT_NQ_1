from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_scope import account_broker, financial_key, text

ATOM_VERSION = "4.3.1"
# v4.3.1 (2026-08-27, item 22/27 of the 27-atom review -- verification
# only, no code change): _on_validated builds an order via one of two
# paths -- _direct_order() (already-priced/sized input) or the sized
# path computed from a stored 513 size. Only the direct path never
# validates stop_loss at all (payload.get("stop_loss") passes straight
# through, even as None or wrong-sided; the sized path DOES check via
# risk_dist <= 0.0). Traced the real pipeline wiring in the manifests,
# not assumed: 552 and 601 (the atom that actually writes to the broker
# bridge) subscribe only to 584's execution.order.legal /
# trading.final_decision, never to this atom's raw execution.order.built
# -- so 584's stop-legality gate is not a parallel observer, it genuinely
# blocks a malformed direct-path order before real execution. An
# end-to-end test with real 551+584 code proves the gap is caught.

EVENT_VALIDATED = "risk.validation.completed"
EVENT_SIZE = "risk.position_size.state"
EVENT_SIZE_REJECTED = "risk.position_size.rejected"
EVENT_ACCOUNT = "platform.account.state"
EVENT_OUT = "execution.order.built"
EVENT_DESIRED = "execution.desired.state"
EVENT_SKIPPED = "execution.order.skipped"

ACTION_OPEN = "OPEN"
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"

REASON_UPSTREAM_REJECTED = "UPSTREAM_REJECTED"
REASON_BAD_SYMBOL_OR_SIDE = "BAD_SYMBOL_OR_SIDE"
REASON_NO_SIZE_YET = "NO_SIZE_YET"
REASON_INCOMPLETE_SIZE_DATA = "INCOMPLETE_SIZE_DATA"
REASON_INVALID_RISK_DISTANCE = "INVALID_RISK_DISTANCE"

# NQ seal item 22, package T (T2): 513's real sizing-rejection reason no
# longer disappears silently -- it rides here as an extra, honest field
# alongside our own categorical skip reason. Never invented: only what 513
# itself published on risk.position_size.rejected.

_PRICE_DP = 6

_DIRECT_FIELDS = (
    "request_id", "account_id", "action", "symbol", "side", "volume",
    "reference_price", "stop_loss", "take_profit", "cycle_id", "origin",
    "pair_id", "leg_role", "attempt", "pair_required", "protection_mode",
    "pair_volume", "purpose", "target_net", "current_net", "delta_net",
    "ticket", "params_json", "logical_symbol", "broker_symbol", "asset_canonical",
    "symbol_resolution_status", "symbol_spec", "snapshot_id",
    "risk_budget", "asset_stop_distance", "broker", "magic",
    # v4.3.0 (2026-08-25): the parent-identity chain crosses this hop AS-IS
    # (layer-3 contract). Measured: the three built orders of 08-19..21
    # carried NONE of these although 576 sent them -- 551 dropped the chain,
    # and 552's snapshot-based recovery was impossible (snapshot_id null).
    # Absent stays absent (never invented); present crosses untouched.
    "decision_id", "gate_request_id", "parent_decision_id",
    "owner_command_id", "session_epoch",
)


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._reward_risk = 2.0
        self._magic = 20260801
        self._sizes: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._size_rejections: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._broker_by_account: dict[str, str] = {}
        self._seen = 0
        self._built = 0
        self._skipped = 0
        self._skip_reasons: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._reward_risk = float(context.config["reward_risk"])
        self._magic = int(context.config.get("magic",20260801))
        context.subscribe(EVENT_VALIDATED, self._on_validated)
        context.subscribe(EVENT_SIZE, self._on_size)
        context.subscribe(EVENT_SIZE_REJECTED, self._on_size_rejected)
        context.subscribe(EVENT_ACCOUNT, self._on_account)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account_id = text(payload.get("account_id"))
        broker = text(payload.get("broker"))
        if account_id and broker:
            self._broker_by_account[account_id] = broker

    async def _on_size(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        key = financial_key(payload, symbol, self._broker_by_account)
        if key is None:
            return
        if payload.get("status") == "REJECTED":
            return
        # T2: a fresh usable size clears any stale sizing rejection for this
        # scope -- the next skip (if any) must not carry a stale reason.
        self._size_rejections.pop(key, None)
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        self._sizes[key] = {
            "price": _to_float(meta.get("price")),
            "buy_lot": _to_float(meta.get("buy_lot")),
            "buy_stop": _to_float(meta.get("buy_stop")),
            "sell_lot": _to_float(meta.get("sell_lot")),
            "sell_stop": _to_float(meta.get("sell_stop")),
        }

    async def _on_size_rejected(self, payload: dict[str, Any]) -> None:
        """T2: remember 513's real sizing-rejection reason per scope, so a
        later skip here (REASON_NO_SIZE_YET) can carry it forward instead of
        losing it -- the reason is exactly what 513 published, never
        invented."""
        if not self._running or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        key = financial_key(payload, symbol, self._broker_by_account)
        if key is None:
            return
        self._size_rejections[key] = {"reason": text(payload.get("reason")) or None}

    def _direct_order(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        symbol = payload.get("symbol")
        side = str(payload.get("side") or "").upper()
        action = str(payload.get("action") or ACTION_OPEN).upper()
        volume = _to_float(payload.get("volume"))
        price = _to_float(payload.get("reference_price"))
        if not symbol or side not in (SIDE_BUY, SIDE_SELL):
            return None
        if volume is None or volume <= 0.0:
            return None
        if action == ACTION_OPEN and (price is None or price <= 0.0):
            return None
        if action != ACTION_OPEN and payload.get("ticket") in (None, "", 0):
            return None
        account = text(payload.get("account_id"))
        broker = text(payload.get("broker")) or self._broker_by_account.get(account, "")
        if not account or not broker:
            return None
        order = {key: payload.get(key) for key in _DIRECT_FIELDS}
        order.update({
            "request_id": str(payload.get("request_id", "")),
            "account_id": account, "broker": broker, "magic":self._magic,
            "action": action,
            "symbol": str(symbol), "side": side, "volume": volume,
            "reference_price": round(price, _PRICE_DP) if price is not None else None,
            "stop_loss": payload.get("stop_loss"),
            "take_profit": payload.get("take_profit"),
        })
        return order

    async def _on_validated(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._seen += 1
        if not payload.get("approved"):
            await self._skip(REASON_UPSTREAM_REJECTED, payload)
            return

        direct = self._direct_order(payload)
        if direct is not None:
            await self._context.publish(EVENT_OUT, direct)
            await self._publish_desired(direct)
            self._built += 1
            return

        side = str(payload.get("side", "")).upper()
        symbol = payload.get("symbol")
        if not symbol or side not in (SIDE_BUY, SIDE_SELL):
            await self._skip(REASON_BAD_SYMBOL_OR_SIDE, payload)
            return
        symbol = str(symbol)
        account = text(payload.get("account_id"))
        broker = text(payload.get("broker")) or self._broker_by_account.get(account, "")
        scope = (account, broker, symbol) if account and broker else None
        size = self._sizes.get(scope) if scope else None
        if size is None:
            rejection = self._size_rejections.get(scope) if scope else None
            await self._skip(REASON_NO_SIZE_YET, payload,
                              sizing_reason=rejection.get("reason") if rejection else None)
            return
        price = size.get("price")
        volume = size.get("buy_lot") if side == SIDE_BUY else size.get("sell_lot")
        stop = size.get("buy_stop") if side == SIDE_BUY else size.get("sell_stop")
        if price is None or volume is None or stop is None or volume <= 0.0:
            await self._skip(REASON_INCOMPLETE_SIZE_DATA, payload)
            return
        risk_dist = (price - stop) if side == SIDE_BUY else (stop - price)
        if risk_dist <= 0.0:
            await self._skip(REASON_INVALID_RISK_DISTANCE, payload)
            return
        target = (price + self._reward_risk * risk_dist) if side == SIDE_BUY \
            else (price - self._reward_risk * risk_dist)
        order = {
            "request_id": str(payload.get("request_id", "")),
            "account_id": account, "broker": broker, "magic":self._magic,
            "action": ACTION_OPEN, "symbol": symbol, "side": side,
            "volume": volume, "reference_price": round(price, _PRICE_DP),
            "stop_loss": round(stop, _PRICE_DP),
            "take_profit": round(target, _PRICE_DP),
            "reward_risk": self._reward_risk,
            "cycle_id": str(payload.get("cycle_id", "")),
        }
        # v4.3.0: parent-identity chain passthrough (layer-3 contract) --
        # present fields cross untouched, absent fields stay absent.
        for chain_field in ("decision_id", "gate_request_id",
                            "parent_decision_id", "owner_command_id",
                            "session_epoch"):
            if payload.get(chain_field) is not None:
                order[chain_field] = payload[chain_field]
        await self._context.publish(EVENT_OUT, order)
        await self._publish_desired(order)
        self._built += 1

    async def _skip(self, reason: str, payload: dict[str, Any],
                    sizing_reason: str | None = None) -> None:
        self._skipped += 1
        self._skip_reasons[reason] = self._skip_reasons.get(reason, 0) + 1
        if self._context is None:
            return
        body = {
            "reason": reason,
            "symbol": payload.get("symbol"),
            "account_id": payload.get("account_id"),
            "request_id": str(payload.get("request_id", "")),
            "cycle_id": str(payload.get("cycle_id", "")),
            # T2: the skipped request keeps its decision identity when the
            # input carried one -- absent stays None, never invented.
            "decision_id": payload.get("decision_id"),
            "gate_request_id": payload.get("gate_request_id"),
        }
        if sizing_reason:
            # T2: 513's real sizing-rejection reason, passed through as-is
            # instead of disappearing behind our own NO_SIZE_YET category.
            body["sizing_reason"] = sizing_reason
        await self._context.publish(EVENT_SKIPPED, body)

    async def _publish_desired(self, order: dict[str, Any]) -> None:
        if self._context is None:
            return
        leg = dict(order)
        leg["leg_id"] = order.get("request_id") or order.get("symbol")
        leg["entry_price"] = order.get("reference_price")
        await self._context.publish(EVENT_DESIRED, {
            "account_id": order.get("account_id"), "broker": order.get("broker"),
            "symbol": order.get("symbol"),
            "asset_canonical": order.get("symbol"),
            "legs": [leg], "leg_id": leg["leg_id"],
            "entry_price": leg["entry_price"], "version": self._built + 1,
            "pair_id": order.get("pair_id"),
            "leg_role": order.get("leg_role"),
        })

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "built": self._built, "skipped": self._skipped,
                   "skip_reasons": dict(self._skip_reasons),
                   "sized_scopes": len(self._sizes),
                   "accounts_with_broker": len(self._broker_by_account)}
        if self._seen == 0:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_RISK_VALIDATION | built=0 skipped=0",
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="built=%d skipped=%d" % (self._built, self._skipped),
                            details=details)
