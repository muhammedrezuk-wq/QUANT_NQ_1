from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.3.0"
# v1.3.0 (2026-08-25): a hard-stop arrival REPLANS. Measured live: the
# portfolio state arrived once at boot (before 525's hard stop replayed),
# the plan froze on MONITOR, and the only path that places a stop on a
# naked leg never fired -- the sleeping-wire pattern (the price was stored
# on line 85 and nothing downstream ever reread it). Now the last portfolio
# payload is kept and every hard-stop update replans from it.

EVENT_PORTFOLIO = "asset.portfolio.state"
EVENT_HARD_STOP = "risk.hard_stop.price"
EVENT_OUT = "perpetual.plan.state"

HARD_STOP_SOURCE = "525"

STATE_FROZEN = "FROZEN"

ACT_FREEZE = "FREEZE"
ACT_HEDGE = "HEDGE"
ACT_HOLD = "HOLD"
ACT_MAINTAIN_STOP = "MAINTAIN_STOP"
ACT_MONITOR = "MONITOR"
ACT_NONE = "NONE"

STOP_MAINTAIN = "MAINTAIN"
STOP_REMOVE_AFTER_HEDGE = "REMOVE_AFTER_HEDGE"
STOP_NONE = "NONE"

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "NO_PORTFOLIO_YET"

_KEY_SEP = "|"
_VOL_DP = 8


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
        self._pstop: dict[str, dict[str, Any]] = {}
        self._last: dict[str, Any] | None = None
        self._last_portfolio: dict[str, Any] | None = None
        self._updates = 0
        self._replans = 0
        self._foreign_source = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_HARD_STOP, self._on_hard_stop)
        context.subscribe(EVENT_PORTFOLIO, self._on_portfolio)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_hard_stop(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        if str(payload.get("source") or "") != HARD_STOP_SOURCE:
            self._foreign_source += 1
            return
        stops = payload.get("stops")
        if not isinstance(stops, list):
            return
        changed = False
        for stop in stops:
            if not isinstance(stop, dict):
                continue
            symbol = str(stop.get("symbol") or "")
            if not symbol:
                continue
            key = str(stop.get("account_id") or "") + _KEY_SEP + symbol
            record = {"p_stop": _to_float(stop.get("p_stop")),
                      "computable": bool(stop.get("computable")),
                      "direction": stop.get("direction")}
            if self._pstop.get(key) != record:
                self._pstop[key] = record
                changed = True
        # v1.3.0: the stop price moving is planning input -- replan from the
        # last portfolio picture instead of waiting for the next one.
        if changed and self._last_portfolio is not None:
            self._replans += 1
            await self._on_portfolio(self._last_portfolio)

    def _plan_for(self, portfolio: dict[str, Any]) -> dict[str, Any]:
        account_id = str(portfolio.get("account_id") or "")
        symbol = str(portfolio.get("symbol") or "")
        key = account_id + _KEY_SEP + symbol
        state = str(portfolio.get("state") or "")
        intent = str(portfolio.get("protection_intent") or ACT_NONE)
        v_net = _to_float(portfolio.get("v_net"))
        pstop = self._pstop.get(key, {})
        protection = ACT_NONE
        hedge_volume = 0.0
        hedge_side = None
        if intent == ACT_FREEZE or state == STATE_FROZEN:
            protection = ACT_FREEZE
        elif intent == "REQUEST_HEDGE":
            protection = ACT_HEDGE
            if v_net is not None and v_net != 0.0:
                hedge_volume = round(abs(v_net), _VOL_DP)
                hedge_side = SIDE_SELL if v_net > 0.0 else SIDE_BUY
        elif intent == "HOLD":
            protection = ACT_HOLD
        if protection == ACT_HEDGE:
            stop_action = STOP_REMOVE_AFTER_HEDGE
            stop_price = None
        elif pstop.get("computable") and pstop.get("p_stop") is not None:
            stop_action = STOP_MAINTAIN
            stop_price = pstop.get("p_stop")
        else:
            stop_action = STOP_NONE
            stop_price = None
        return {"account_id": account_id, "symbol": symbol, "state": state,
                "primary_action": self._primary(protection, stop_action),
                "protection": protection, "hedge_volume": hedge_volume,
                "hedge_side": hedge_side, "stop_action": stop_action,
                "stop_price": stop_price, "v_net": v_net}

    def _primary(self, protection: str, stop_action: str) -> str:
        if protection == ACT_FREEZE:
            return ACT_FREEZE
        if protection == ACT_HEDGE:
            return ACT_HEDGE
        if protection == ACT_HOLD:
            return ACT_HOLD
        if stop_action == STOP_MAINTAIN:
            return ACT_MAINTAIN_STOP
        return ACT_MONITOR

    async def _on_portfolio(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        portfolios = payload.get("portfolios")
        if not isinstance(portfolios, list):
            return
        self._last_portfolio = dict(payload)
        plans = [self._plan_for(p) for p in portfolios if isinstance(p, dict) and p.get("symbol")]
        out: dict[str, Any] = {"plans": plans, "count": len(plans),
                               "halted": bool(payload.get("halted"))}
        stamp = payload.get("timestamp")
        if stamp is not None:
            out["timestamp"] = stamp
        self._last = out
        self._updates += 1
        await self._context.publish(EVENT_OUT, out)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"updates": self._updates, "stops_cached": len(self._pstop),
                   "hard_stop_source": HARD_STOP_SOURCE,
                   "foreign_source_ignored": self._foreign_source}
        if self._last is None:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="plans=%d" % self._last.get("count", 0), details=details)
