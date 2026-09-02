from __future__ import annotations

import math
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "4.1.0"

EVENT_IN = "market.tick"
EVENT_ACCOUNT = "platform.account.state"
EVENT_ALERT = "market_data.quality_alert"
EVENT_STATE = "market_data.quality.state"
EVENT_OUT = EVENT_ALERT

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "NO_TICKS_YET"

KIND_DUPLICATE = "duplicate"
KIND_GAP = "time_gap"
KIND_SPIKE = "price_spike"
KIND_SPREAD = "abnormal_spread"

STATUS_INVALID = "INVALID"
STATUS_DEGRADED = "DEGRADED"
STATUS_HEALTHY = "HEALTHY"

PCT_SCALE = 100.0
_MID_DIVISOR = 2.0


def num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._gap_threshold_s = 0.0
        self._spike_pct = 0.0
        self._spread_pct = 0.0
        self._brokers: dict[str, str] = {}
        self._last_price: dict[tuple, float] = {}
        self._last_ts: dict[tuple, float] = {}
        self._last_key: dict[tuple, tuple] = {}
        self._checked = 0
        self._alerts = 0
        self._invalid = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        config = context.config
        self._gap_threshold_s = float(config["gap_threshold_s"])
        self._spike_pct = float(config["spike_pct"])
        self._spread_pct = float(config["spread_pct"])
        context.subscribe(EVENT_IN, self._on_tick)
        context.subscribe(EVENT_ACCOUNT, self._on_account)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_account(self, payload: Any) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        if account and broker:
            self._brokers[account] = broker

    async def _state(self, payload: dict, status: str, reasons: list) -> None:
        if self._context is None:
            return
        body = {
            "account_id": payload.get("account_id"),
            "broker": payload.get("broker") or self._brokers.get(
                str(payload.get("account_id") or "")),
            "symbol": payload.get("symbol"),
            "status": status,
            "usable_for_decision": status != STATUS_INVALID,
            "reasons": reasons,
        }
        timestamp = num(payload.get("timestamp"))
        if timestamp is not None:
            body["timestamp"] = timestamp
        await self._context.publish(EVENT_STATE, body)

    def _invalid_reasons(self, symbol: str, account: str, broker: str,
                         bid: float | None, ask: float | None,
                         timestamp: float | None) -> list:
        reasons = []
        if not symbol:
            reasons.append("missing_symbol")
        if not account:
            reasons.append("missing_account_id")
        if not broker:
            reasons.append("missing_broker")
        if bid is None or ask is None:
            reasons.append("non_finite_price")
        elif bid <= 0 or ask <= 0:
            reasons.append("nonpositive_price")
        elif ask < bid:
            reasons.append("crossed_spread")
        if timestamp is None or timestamp <= 0:
            reasons.append("invalid_timestamp")
        return reasons

    async def _on_tick(self, payload: Any) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        account = str(payload.get("account_id") or "")
        broker = str(payload.get("broker") or "") or self._brokers.get(account, "")
        scope = (account, broker, symbol)
        bid = num(payload.get("bid"))
        ask = num(payload.get("ask"))
        timestamp = num(payload.get("timestamp"))
        self._checked += 1
        if (not symbol or not account or not broker
                or bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid
                or timestamp is None or timestamp <= 0):
            self._invalid += 1
            await self._state(payload, STATUS_INVALID, self._invalid_reasons(
                symbol, account, broker, bid, ask, timestamp))
            return

        reasons = []
        price = (bid + ask) / _MID_DIVISOR
        key = (bid, ask, timestamp)
        if key == self._last_key.get(scope):
            reasons.append(KIND_DUPLICATE)
            await self._alert(KIND_DUPLICATE, payload, {}, timestamp)
        self._last_key[scope] = key

        previous_ts = self._last_ts.get(scope)
        if previous_ts is not None and timestamp - previous_ts > self._gap_threshold_s:
            reasons.append(KIND_GAP)
            await self._alert(KIND_GAP, payload,
                              {"gap_s": round(timestamp - previous_ts, 2)}, timestamp)
        self._last_ts[scope] = timestamp

        previous_price = self._last_price.get(scope)
        if previous_price and abs(price - previous_price) / previous_price * PCT_SCALE > self._spike_pct:
            change = abs(price - previous_price) / previous_price * PCT_SCALE
            reasons.append(KIND_SPIKE)
            await self._alert(KIND_SPIKE, payload,
                              {"change_pct": round(change, 3)}, timestamp)
        self._last_price[scope] = price

        spread = (ask - bid) / price * PCT_SCALE
        if spread > self._spread_pct:
            reasons.append(KIND_SPREAD)
            await self._alert(KIND_SPREAD, payload,
                              {"spread_pct": round(spread, 4)}, timestamp)

        await self._state(payload, STATUS_DEGRADED if reasons else STATUS_HEALTHY,
                          reasons)

    async def _alert(self, kind: str, payload: dict, details: dict,
                     timestamp: float | None) -> None:
        self._alerts += 1
        account = str(payload.get("account_id") or "")
        body = {
            "kind": kind,
            "account_id": account,
            "broker": payload.get("broker") or self._brokers.get(account),
            "symbol": payload.get("symbol"),
            **details,
        }
        if timestamp is not None:
            body["timestamp"] = timestamp
        await self._context.publish(EVENT_ALERT, body)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY,
                                message=REASON_NOT_STARTED)
        if self._checked == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA)
        state = HealthState.DEGRADED if self._invalid else HealthState.HEALTHY
        return HealthStatus(
            state=state,
            message="checked=%d alerts=%d invalid=%d" % (
                self._checked, self._alerts, self._invalid),
            details={"checked": self._checked, "alerts": self._alerts,
                     "invalid": self._invalid})
