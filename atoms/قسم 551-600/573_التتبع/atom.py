from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_scope import financial_key, text

ATOM_VERSION = "2.0.0"

EVENT_POSITIONS = "platform.positions.state"
EVENT_STOP = "risk.structure_stop.state"
EVENT_ACCOUNT = "platform.account.state"
EVENT_OUT = "execution.manage.intent"

ACTION_MODIFY = "MODIFY_SL"
REASON_TRAIL = "trailing"

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_POSITIONS = "NO_POSITIONS_YET"

_R_DP = 4


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
        self._start_r = 1.0
        self._broker_by_account: dict[str, str] = {}
        self._stops: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._tracked: dict[tuple[str, str], dict[str, Any]] = {}
        self._seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._start_r = float(context.config["trail_start_r"])
        context.subscribe(EVENT_POSITIONS, self._on_positions)
        context.subscribe(EVENT_STOP, self._on_stop)
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
        account = text(payload.get("account_id")); broker = text(payload.get("broker"))
        if account and broker:
            self._broker_by_account[account] = broker

    async def _on_stop(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        key = financial_key(payload, symbol, self._broker_by_account)
        if key is None:
            return
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        self._stops[key] = {"buy_stop": _to_float(meta.get("buy_stop")),
                            "sell_stop": _to_float(meta.get("sell_stop"))}

    def _track(self, ticket_key: tuple[str, str], entry: float, stop: float) -> dict[str, Any]:
        st = self._tracked.get(ticket_key)
        if st is None:
            risk = abs(entry - stop) if stop and stop > 0.0 else None
            st = {"risk": risk, "last_sl": None}
            self._tracked[ticket_key] = st
        return st

    def _trail_target(self, side: str, scope: tuple[str, str, str], current: float, cur_sl: float,
                      last_sl: Any) -> float | None:
        stops = self._stops.get(scope)
        if stops is None:
            return None
        if side == SIDE_BUY:
            target = stops.get("buy_stop")
            if target is None or target >= current:
                return None
            floor = cur_sl if cur_sl > 0.0 else 0.0
            if last_sl is not None and last_sl > floor:
                floor = last_sl
            return target if target > floor else None
        target = stops.get("sell_stop")
        if target is None or target <= current:
            return None
        ceiling = None
        if cur_sl > 0.0:
            ceiling = cur_sl
        if last_sl is not None and (ceiling is None or last_sl < ceiling):
            ceiling = last_sl
        if ceiling is not None and target >= ceiling:
            return None
        return target

    async def _on_positions(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        positions = payload.get("positions")
        if not isinstance(positions, list):
            return
        self._seen += 1
        live: set[tuple[str, str]] = set()
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            ticket = pos.get("ticket")
            side = str(pos.get("side", ""))
            entry = _to_float(pos.get("entry_price"))
            current = _to_float(pos.get("current_price"))
            stop = _to_float(pos.get("stop_loss"))
            symbol = pos.get("symbol")
            if ticket is None or entry is None or current is None or not symbol \
                    or side not in (SIDE_BUY, SIDE_SELL):
                continue
            account = text(pos.get("account_id") or payload.get("account_id"))
            broker = text(pos.get("broker") or payload.get("broker")) or self._broker_by_account.get(account, "")
            scope = financial_key({"account_id": account, "broker": broker}, symbol, self._broker_by_account)
            if scope is None:
                continue
            key = (account, str(ticket))
            live.add(key)
            cur_sl = stop if stop is not None else 0.0
            st = self._track(key, entry, cur_sl)
            if st["risk"] is None or st["risk"] <= 0.0:
                continue
            profit_distance = (current - entry) if side == SIDE_BUY else (entry - current)
            if profit_distance / st["risk"] < self._start_r:
                continue
            target = self._trail_target(side, scope, current, cur_sl, st["last_sl"])
            if target is None:
                continue
            st["last_sl"] = target
            await self._context.publish(EVENT_OUT, {
                "account_id": account, "broker": broker,
                "ticket": ticket, "symbol": str(symbol), "side": side,
                "action": ACTION_MODIFY, "stop_loss": target, "reason": REASON_TRAIL,
                "r_multiple": round(profit_distance / st["risk"], _R_DP)})
            self._emitted += 1
        for key in list(self._tracked):
            if key not in live:
                del self._tracked[key]

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"tracked": len(self._tracked), "stops": len(self._stops),
                   "seen": self._seen, "emitted": self._emitted}
        if self._seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_POSITIONS,
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="trails=%d tracked=%d" % (
                                self._emitted, len(self._tracked)), details=details)
