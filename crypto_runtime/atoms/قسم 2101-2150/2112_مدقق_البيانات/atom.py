from __future__ import annotations

import math
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "4.0.0"

EVENT_TICK = "market.tick"
EVENT_ACCOUNT = "platform.account.state"
EVENT_VALID = "market.tick.validated"
EVENT_INVALID = "market_data.validation_failed"
EVENT_STATE = "market_data.validation.state"
EVENT_OUT = EVENT_INVALID

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TICKS = "NO_TICKS_YET"

CONTRACT_FIELDS = (
    "symbol", "bid", "ask", "price", "volume", "provider", "timestamp",
    "exchange_timestamp", "received_at", "account_id",
)


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._broker_by_account: dict[str, str] = {}
        self.checked_count = 0
        self.valid_count = 0
        self.failed_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_TICK, self._on_tick)
        context.subscribe(EVENT_ACCOUNT, self._on_account)

    async def start(self) -> None:
        self._running = True
        await self._publish_state("ACTIVE")

    async def stop(self) -> None:
        if self._running:
            self._running = False
            await self._publish_state("STOPPED")

    async def shutdown(self) -> None:
        await self.stop()

    async def _publish_state(self, status: str) -> None:
        if self._context is not None:
            await self._context.publish(EVENT_STATE, {
                "status": status,
                "usable_for_decision": status == "ACTIVE",
                "checked": self.checked_count,
                "valid": self.valid_count,
                "invalid": self.failed_count,
            })

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        if account and broker:
            self._broker_by_account[account] = broker

    @staticmethod
    def _validate(payload: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        symbol = str(payload.get("symbol") or "").strip()
        account = str(payload.get("account_id") or "").strip()
        provider = str(payload.get("provider") or "").strip()
        if not symbol:
            problems.append("missing_symbol")
        if not account:
            problems.append("missing_account_id")
        if not provider:
            problems.append("missing_provider")

        bid = number(payload.get("bid"))
        ask = number(payload.get("ask"))
        if bid is None:
            problems.append("invalid_or_missing_bid")
        elif bid <= 0:
            problems.append("nonpositive_bid")
        if ask is None:
            problems.append("invalid_or_missing_ask")
        elif ask <= 0:
            problems.append("nonpositive_ask")
        if bid is not None and ask is not None and ask < bid:
            problems.append("crossed_spread")

        price = number(payload.get("price"))
        if price is None:
            problems.append("invalid_or_missing_price")
        elif price <= 0:
            problems.append("nonpositive_price")

        stamp = number(payload.get("timestamp"))
        if stamp is None or stamp <= 0:
            problems.append("invalid_timestamp")
        for field in ("exchange_timestamp", "received_at"):
            if payload.get(field) is not None:
                value = number(payload.get(field))
                if value is None or value <= 0:
                    problems.append("invalid_" + field)
        if payload.get("volume") is not None:
            volume = number(payload.get("volume"))
            if volume is None:
                problems.append("invalid_volume")
            elif volume < 0:
                problems.append("negative_volume")
        return list(dict.fromkeys(problems))

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self.checked_count += 1
        problems = self._validate(payload)
        if problems:
            self.failed_count += 1
            await self._context.publish(EVENT_INVALID, {
                "source_event": EVENT_TICK,
                "status": "INVALID",
                "problems": problems,
                "payload": dict(payload),
            })
            return

        validated = dict(payload)
        account = str(payload.get("account_id") or "")
        broker = self._broker_by_account.get(account)
        if broker and not validated.get("broker"):
            validated["broker"] = broker
        self.valid_count += 1
        await self._context.publish(EVENT_VALID, validated)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"checked": self.checked_count, "valid": self.valid_count,
                   "invalid": self.failed_count,
                   "accounts_with_broker": len(self._broker_by_account)}
        if self.checked_count == 0:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_TICKS, details=details)
        state = HealthState.DEGRADED if self.failed_count and not self.valid_count else HealthState.HEALTHY
        return HealthStatus(state=state,
                            message="checked=%d valid=%d invalid=%d" % (
                                self.checked_count, self.valid_count, self.failed_count),
                            details=details)
