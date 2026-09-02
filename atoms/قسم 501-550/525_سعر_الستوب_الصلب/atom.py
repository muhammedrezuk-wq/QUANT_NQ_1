from __future__ import annotations

import math
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_scope import account_broker, financial_key, row_key, text

ATOM_VERSION = "2.1.0"
# v2.1.0 (2026-08-27): min_abs_v_net governs the flat-position guard right
# before `p_stop`/`delta_p` divide by v_net -- but it is owner-configurable
# and the schema's own `minimum: 0` allows it to be set to exactly 0
# (a plausible misreading: "0 = no filtering"). At 0 the guard
# `abs(v_net) < min_abs_v_net` becomes `abs(v_net) < 0`, never true, so a
# genuinely flat position (v_net == 0.0) reaches the division --
# ZeroDivisionError, config-permitted, not a contrived edge case. A hard,
# non-configurable floor below protects the division regardless of what
# the owner sets; the config value can still raise the threshold for
# business reasons, just never lower it past this floor.
_HARD_MIN_ABS_V_NET = 1e-9
EVENT_LEDGER = "risk.asset_ledger.state"
EVENT_SPECS = "market.symbol_specs"
EVENT_ACCOUNT = "platform.account.state"
EVENT_OUT = "risk.hard_stop.price"


def number(value: Any) -> float | None:
    if isinstance(value, bool): return None
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None; self._running = False
        self._min_abs_v_net = 1e-9
        self._broker_by_account: dict[str, str] = {}
        self._pending_specs: list[dict[str, Any]] = []
        self._vpu: dict[tuple[str, str, str], float] = {}
        self._last: dict[str, Any] | None = None; self._updates = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context; self._min_abs_v_net = float(context.config.get("min_abs_v_net", 1e-9))
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        context.subscribe(EVENT_SPECS, self._on_specs)
        context.subscribe(EVENT_LEDGER, self._on_ledger)

    async def start(self) -> None: self._running = True
    async def stop(self) -> None: self._running = False
    async def shutdown(self) -> None: await self.stop()

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account = text(payload.get("account_id")); broker = text(payload.get("broker"))
        if account and broker:
            self._broker_by_account[account] = broker
            pending, self._pending_specs = self._pending_specs, []
            for item in pending: await self._on_specs(item)

    async def _on_specs(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        rows = payload.get("symbols")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict): continue
            key = row_key(payload, row, self._broker_by_account)
            tick_value = number(row.get("tick_value")); tick_size = number(row.get("tick_size"))
            if key is not None and tick_value is not None and tick_size is not None and tick_value > 0 and tick_size > 0:
                self._vpu[key] = tick_value / tick_size
            elif text(row.get("account_id") or payload.get("account_id")) and payload not in self._pending_specs:
                self._pending_specs.append(dict(payload))

    def _compute(self, led: dict[str, Any]) -> dict[str, Any]:
        key = financial_key(led, led.get("symbol"), self._broker_by_account)
        account = text(led.get("account_id")); broker = text(led.get("broker")) or self._broker_by_account.get(account, "")
        symbol = text(led.get("symbol")); v_net = number(led.get("v_net")); w = number(led.get("w"))
        budget = number(led.get("budget")); buffer_k = number(led.get("buffer_k")) or 0.0
        commission = number(led.get("commission_est")) or 0.0
        vpu = self._vpu.get(key) if key is not None else None
        base = {"account_id": account, "broker": broker, "symbol": symbol, "v_net": v_net,
                "budget": budget, "buffer_k": buffer_k, "commission_est": commission,
                "computable": False, "p_stop": None, "delta_p": None,
                "avg_entry": None, "direction": None, "vpu": vpu}
        if key is None: base["reason"] = "MISSING_ACCOUNT_BROKER_OR_SYMBOL"; return base
        if not bool(led.get("budgeted")) or budget is None or budget <= 0:
            base["reason"] = "NO_BUDGET"; return base
        if vpu is None or vpu <= 0: base["reason"] = "NO_ACCOUNT_SYMBOL_SPECS"; return base
        if v_net is None or w is None or abs(v_net) < max(self._min_abs_v_net, _HARD_MIN_ABS_V_NET):
            base["reason"] = "FLAT_NO_PRICE_STOP"; return base
        room = budget + buffer_k - commission
        delta_p = room / (vpu * abs(v_net)); p_stop = (w + (-budget-buffer_k+commission)/vpu)/v_net
        base.update({"computable": True, "reason": "OK", "p_stop": round(p_stop, 8),
                     "delta_p": round(delta_p, 8), "avg_entry": round(w/v_net, 8),
                     "direction": "LONG" if v_net > 0 else "SHORT", "vpu": vpu})
        return base

    async def _on_ledger(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        rows = payload.get("ledgers")
        if not isinstance(rows, list): return
        stops = [self._compute(row) for row in rows if isinstance(row, dict) and row.get("symbol")]
        out = {"stops": stops, "count": len(stops),
               "computable_count": sum(1 for x in stops if x["computable"])}
        if payload.get("timestamp") is not None: out["timestamp"] = payload["timestamp"]
        self._last = out; self._updates += 1; await self._context.publish(EVENT_OUT, out)

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"updates": self._updates, "scoped_specs": len(self._vpu),
                   "computable": (self._last or {}).get("computable_count")}
        if self._last is None: return HealthStatus(state=HealthState.DEGRADED, message="NO_LEDGER_YET", details=details)
        missing = any(x.get("reason") in {"NO_ACCOUNT_SYMBOL_SPECS", "MISSING_ACCOUNT_BROKER_OR_SYMBOL"}
                      for x in self._last.get("stops", []))
        return HealthStatus(state=HealthState.DEGRADED if missing else HealthState.HEALTHY,
                            message="MISSING_SCOPED_SPECS" if missing else "hard_stops_computed",
                            details=details)
