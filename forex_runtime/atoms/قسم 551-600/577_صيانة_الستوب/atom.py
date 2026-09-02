from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"
# v1.1.0 (2026-08-25): the manage command carries the leg's magic. Measured
# on the live orphan leg: 577's MODIFY_SL reached 575 without a magic field
# and died there as MISSING_OR_FOREIGN_MAGIC -- the only path that places a
# hard stop on a position was broken at its last meter. The magic rides
# from the broker's own position row (609 passes it through), so a foreign
# position still gets refused by 575's ownership checks, never adopted.

EVENT_PLAN = "perpetual.plan.state"
EVENT_POSITIONS = "platform.positions.state"
EVENT_MANAGE = "execution.manage.command"

ACTION_MODIFY = "MODIFY_SL"
ACT_MAINTAIN = "MAINTAIN_STOP"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "NO_PLAN_YET"

_KEY_SEP = "|"


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_side(side: Any) -> str:
    text = str(side).strip().lower()
    if text in ("sell", "short", "1"):
        return "SELL"
    if text in ("buy", "long", "0"):
        return "BUY"
    return ""


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._min_change = 1e-9
        self._legs: dict[str, list[dict[str, Any]]] = {}
        self._last_sl: dict[int, float] = {}
        self._sent = 0
        self._updates = 0
        self._seen_plan = False

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._min_change = float(context.config.get("min_sl_change", 1e-9))
        context.subscribe(EVENT_POSITIONS, self._on_positions)
        context.subscribe(EVENT_PLAN, self._on_plan)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_positions(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        positions = payload.get("positions")
        if not isinstance(positions, list):
            return
        legs: dict[str, list[dict[str, Any]]] = {}
        live_tickets: set[int] = set()
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            symbol = str(pos.get("symbol") or "")
            ticket = _to_int(pos.get("ticket"))
            side = _norm_side(pos.get("side"))
            if not symbol or ticket is None or not side:
                continue
            key = str(pos.get("account_id") or "") + _KEY_SEP + symbol
            legs.setdefault(key, []).append({"ticket": ticket, "side": side,
                                             "magic": _to_int(pos.get("magic"))})
            live_tickets.add(ticket)
        self._legs = legs
        for ticket in [t for t in self._last_sl if t not in live_tickets]:
            del self._last_sl[ticket]

    async def _on_plan(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        plans = payload.get("plans")
        if not isinstance(plans, list):
            return
        self._seen_plan = True
        self._updates += 1
        for plan in plans:
            if isinstance(plan, dict):
                await self._maintain(plan)

    async def _maintain(self, plan: dict[str, Any]) -> None:
        if str(plan.get("primary_action") or "") != ACT_MAINTAIN:
            return
        stop_price = _to_float(plan.get("stop_price"))
        v_net = _to_float(plan.get("v_net"))
        symbol = str(plan.get("symbol") or "")
        if stop_price is None or v_net is None or v_net == 0.0 or not symbol:
            return
        net_side = "BUY" if v_net > 0.0 else "SELL"
        key = str(plan.get("account_id") or "") + _KEY_SEP + symbol
        for leg in self._legs.get(key, []):
            if leg["side"] != net_side:
                continue
            ticket = leg["ticket"]
            prev = self._last_sl.get(ticket)
            if prev is not None and abs(prev - stop_price) < self._min_change:
                continue
            self._last_sl[ticket] = stop_price
            self._sent += 1
            await self._context.publish(EVENT_MANAGE, {
                "account_id": key.split(_KEY_SEP, 1)[0],
                "action": ACTION_MODIFY, "ticket": ticket, "symbol": symbol,
                "side": leg["side"], "stop_loss": stop_price,
                "magic": leg.get("magic"), "origin": "perpetual"})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"updates": self._updates, "sent": self._sent,
                   "tracked_legs": sum(len(v) for v in self._legs.values())}
        if not self._seen_plan:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="modify_sent=%d" % self._sent, details=details)
