# -*- coding: utf-8 -*-
"""Simulated Execution Bridge (626) — X.md Build 2, owner choice (A) 2026-08-23.

Receives final approved orders (``trading.final_decision`` — the same outbound
edge the live broker bridges consume) and answers with computed broker events
on ``platform.trade_event`` — the same inbound edge 611 publishes live. It
never opens a connection and writes nothing.

Fill model (owner seal 2026-08-23: from the RECORDED spread, not a fixed
number): slippage = (ask − bid) / 2 taken from the latest validated tick of
that symbol; a BUY fills worse by +slip, a SELL by −slip. With no recorded
tick yet the fill uses the order's reference price with slip = 0 and the
reason ``SPREAD_UNAVAILABLE_REFERENCE_ONLY`` is counted — never silent.

Determinism: trade_id is ``sim-<request_id>`` and the reply is immediate —
replaying the same segment yields byte-equal execution events.
"""

from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_ORDERS = "trading.final_decision"
EVENT_TICK = "market.tick.validated"
# م-55 (ورقة ٤١، بأمر المالك «صلّح» 2026-08-28): كان الخروج على حدث القارئ
# الحقيقي نفسه (platform.trade_event) — لا مستهلكًا واحدًا من الـ17 يفحص حقل
# simulated، فتُفرَج حجوزات 516/585 الحقيقية فورًا بتعبئة وهمية. صار الخروج
# على حدث محاكاة مستقل لا يصل مسار المخاطر أبدًا (عزل بالتسمية لا بالنواية).
EVENT_OUT = "platform.trade_event.simulated"
EVENT_STATE = "sim.execution.state"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_ORDERS = "NO_ORDERS_YET"
ACTION_OPEN = "OPEN"
SIDE_BUY = "BUY"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _text(value: Any) -> str:
    return str(value or "").strip()


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._per_lot_commission = 0.0
        self._latency_ms = 0
        self._fills = 0
        self._orders_seen = 0
        # X.md Build 3: every drop is counted with its reason code.
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}
        # symbol -> (bid, ask) from the latest validated (recorded) tick
        self._last_spread: dict[str, tuple[float, float]] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._per_lot_commission = max(0.0, float(cfg.get("per_lot_commission") or 0.0))
        self._latency_ms = max(0, int(cfg.get("latency_ms") or 0))
        context.subscribe(EVENT_ORDERS, self._on_order)
        context.subscribe(EVENT_TICK, self._on_tick)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = _text(payload.get("symbol"))
        bid, ask = _number(payload.get("bid")), _number(payload.get("ask"))
        if not symbol or bid is None or ask is None or ask < bid:
            self._drop("SPREAD_UNMEASURABLE"); return
        self._last_spread[symbol] = (bid, ask)

    async def _on_order(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        request_id = _text(payload.get("request_id"))
        symbol = _text(payload.get("symbol"))
        account = _text(payload.get("account_id"))
        broker = _text(payload.get("broker"))
        if not request_id or not symbol or not account or not broker:
            self._drop("ORDER_IDENTITY_MISSING"); return
        self._orders_seen += 1
        action = _text(payload.get("action")).upper() or ACTION_OPEN
        if action != ACTION_OPEN:
            # OPEN is fully simulated; other actions are DECLARED uncounted-as-
            # fills, never silently swallowed.
            self._drop("ACTION_NOT_SIMULATED"); return

        side = _text(payload.get("side")).upper() or SIDE_BUY
        reference = _number(payload.get("reference_price"))
        volume = _number(payload.get("volume")) or 0.0
        spread = self._last_spread.get(symbol)
        if reference is None:
            self._drop("ORDER_REFERENCE_PRICE_MISSING"); return
        slip_source = "recorded_spread"
        if spread is None:
            slip, slip_source = 0.0, "reference_only"
            self._drop("SPREAD_UNAVAILABLE_REFERENCE_ONLY")
        else:
            slip = (spread[1] - spread[0]) / 2.0
        entry = reference + slip if side == SIDE_BUY else reference - slip
        commission = round(self._per_lot_commission * volume, 6)
        self._fills += 1
        await self._context.publish(EVENT_OUT, {
            "account_id": account, "broker": broker, "symbol": symbol,
            "request_id": request_id, "trade_id": f"sim-{request_id}",
            "event_type": "OPENED", "side": side, "volume": volume,
            "entry_price": entry, "requested_price": reference,
            "slippage": slip, "slippage_source": slip_source,
            "commission": commission, "simulated": True,
            "latency_ms": self._latency_ms,
            "timestamp": payload.get("reference_timestamp"),
        })
        await self._context.publish(EVENT_STATE, {
            "orders_seen": self._orders_seen, "fills": self._fills,
            "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons)})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"orders_seen": self._orders_seen, "fills": self._fills,
                   "dropped": self._dropped,
                   "drop_reasons": dict(self._drop_reasons),
                   "symbols_with_spread": len(self._last_spread)}
        if not self._orders_seen:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_ORDERS, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=f"orders={self._orders_seen} fills={self._fills}",
                            details=details)
