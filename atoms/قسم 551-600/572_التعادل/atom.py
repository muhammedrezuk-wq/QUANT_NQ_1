from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.1"

EVENT_IN = "platform.positions.state"
EVENT_OUT = "execution.manage.intent"

ACTION_MODIFY = "MODIFY_SL"
REASON_BREAKEVEN = "breakeven"

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
        self._at_r = 1.0
        self._tracked: dict[str, dict[str, Any]] = {}
        self._seen = 0
        self._emitted = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._at_r = float(context.config["breakeven_at_r"])
        context.subscribe(EVENT_IN, self._on_positions)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _track(self, ticket: str, entry: float, stop: float, side: str) -> dict[str, Any]:
        st = self._tracked.get(ticket)
        if st is None:
            risk = abs(entry - stop) if stop and stop > 0.0 else None
            st = {"entry": entry, "side": side, "risk": risk, "done": False}
            self._tracked[ticket] = st
        return st

    async def _on_positions(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        positions = payload.get("positions")
        if not isinstance(positions, list):
            return
        self._seen += 1
        live: set[str] = set()
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            ticket = pos.get("ticket")
            side = str(pos.get("side", ""))
            entry = _to_float(pos.get("entry_price"))
            current = _to_float(pos.get("current_price"))
            stop = _to_float(pos.get("stop_loss"))
            symbol = pos.get("symbol")
            if ticket is None or entry is None or current is None or side not in (SIDE_BUY, SIDE_SELL):
                continue
            key = str(ticket)
            live.add(key)
            st = self._track(key, entry, stop if stop is not None else 0.0, side)
            if st["done"] or st["risk"] is None or st["risk"] <= 0.0:
                continue
            profit_distance = (current - entry) if side == SIDE_BUY else (entry - current)
            r_multiple = profit_distance / st["risk"]
            if r_multiple < self._at_r:
                continue
            new_sl = entry
            cur_sl = stop if stop is not None else 0.0
            tighter = (side == SIDE_BUY and new_sl > cur_sl) or \
                      (side == SIDE_SELL and (cur_sl <= 0.0 or new_sl < cur_sl))
            if not tighter:
                st["done"] = True
                continue
            await self._context.publish(EVENT_OUT, {
                "account_id": str(pos.get("account_id") or ""),
                "ticket": ticket, "symbol": str(symbol) if symbol else "", "side": side,
                "action": ACTION_MODIFY, "stop_loss": new_sl,
                "reason": REASON_BREAKEVEN, "r_multiple": round(r_multiple, _R_DP)})
            st["done"] = True
            self._emitted += 1
        for key in list(self._tracked):
            if key not in live:
                del self._tracked[key]

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"tracked": len(self._tracked), "seen": self._seen, "emitted": self._emitted}
        if self._seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_POSITIONS,
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="breakevens=%d tracked=%d" % (
                                self._emitted, len(self._tracked)), details=details)
