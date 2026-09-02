from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.1.2"

EVENT_BUILT = "execution.order.built"
EVENT_FINAL = "trading.final_decision"
EVENT_REJECTED = "execution.order.rejected"
EVENT_SKIPPED = "execution.order.skipped"
EVENT_BRIDGE_WRITTEN = "platform.brain_signal.written"
EVENT_BRIDGE_FAILED = "platform.brain_signal.write_failed"
EVENT_ACK = "execution.command.ack"
EVENT_COMMAND_FAILED = "execution.command.failed"
EVENT_TRADE = "platform.trade_event"
EVENT_MANAGE_CMD = "execution.manage.command"
EVENT_MANAGE_WRITTEN = "execution.manage.written"
EVENT_OUTCOME = "market.outcome.realized"
EVENT_HALT = "emergency.halt"
EVENT_RESET = "risk.kill_switch.reset_requested"
EVENT_OUT = "execution.unified.state"

STAGE_BUILT = "ORDER_BUILT"
STAGE_FINALIZED = "DECISION_FINALIZED"
STAGE_QUEUED = "QUEUED_TO_BRIDGE"
STAGE_ACK = "BROKER_ACKNOWLEDGED"
STAGE_FAILED = "BROKER_COMMAND_FAILED"
STAGE_REJECTED = "ORDER_REJECTED"
STAGE_SKIPPED = "ORDER_SKIPPED"
STAGE_FILLED_OPEN = "FILLED_OPEN"
STAGE_FILLED_PARTIAL = "FILLED_PARTIAL"
STAGE_FILLED_CLOSED = "FILLED_CLOSED"

_STAGE_BY_TRADE = {
    "OPENED": STAGE_FILLED_OPEN,
    "PARTIAL": STAGE_FILLED_PARTIAL,
    "CLOSED": STAGE_FILLED_CLOSED,
}
_STAGE_RANK = {
    STAGE_SKIPPED: 1, STAGE_BUILT: 1, STAGE_FINALIZED: 2, STAGE_QUEUED: 3, STAGE_ACK: 4,
    STAGE_REJECTED: 5, "BRIDGE_WRITE_FAILED": 5, STAGE_FAILED: 5,
    STAGE_FILLED_OPEN: 6, STAGE_FILLED_PARTIAL: 6, STAGE_FILLED_CLOSED: 6,
}
_COUNT_KEYS = (
    "built", "order_skipped", "decision_finalized", "queued_to_bridge", "broker_acknowledged",
    "bridge_write_failed", "broker_command_failed", "rejected", "filled_open", "filled_partial",
    "filled_closed", "manage_commands", "manage_queued", "outcomes", "halts",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _text(value: Any) -> str:
    return str(value or "").strip()


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._counts = {key: 0 for key in _COUNT_KEYS}
        self._counts_by_account: dict[str, dict[str, int]] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._last_outcome_by_account: dict[str, dict[str, Any]] = {}
        self._halted_accounts: dict[str, str] = {}
        self._reject_reasons: dict[str, int] = {}
        self._skip_reasons: dict[str, int] = {}
        self._restore_error = ""
        self._rehydrated = False
        self._seen = 0
        self._identity_dropped = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        for event, handler in (
            (EVENT_BUILT, self._on_built), (EVENT_FINAL, self._on_final),
            (EVENT_REJECTED, self._on_rejected), (EVENT_SKIPPED, self._on_skipped),
            (EVENT_BRIDGE_WRITTEN, self._on_bridge_written),
            (EVENT_BRIDGE_FAILED, self._on_bridge_failed),
            (EVENT_ACK, self._on_ack), (EVENT_COMMAND_FAILED, self._on_command_failed),
            (EVENT_TRADE, self._on_trade), (EVENT_MANAGE_CMD, self._on_manage_cmd),
            (EVENT_MANAGE_WRITTEN, self._on_manage_written),
            (EVENT_OUTCOME, self._on_outcome), (EVENT_HALT, self._on_halt),
            (EVENT_RESET, self._on_reset), (EVENT_OUT, self._rehydrate),
        ):
            context.subscribe(event, handler)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    @staticmethod
    def _key(account: str, request_id: str) -> str:
        return account + "\x1f" + request_id

    def _account_counts(self, account: str) -> dict[str, int]:
        return self._counts_by_account.setdefault(account, {key: 0 for key in _COUNT_KEYS})

    def _summary(self, payload: dict[str, Any], stage: str) -> dict[str, Any]:
        return {
            "account_id": _text(payload.get("account_id")),
            "broker": _text(payload.get("broker")),
            "request_id": _text(payload.get("request_id") or payload.get("command_id")),
            "stage": stage,
            "symbol": _text(payload.get("symbol")),
            "side": _text(payload.get("side")),
            "action": _text(payload.get("action")),
            "volume": _number(payload.get("volume")),
            "ticket": payload.get("ticket"),
            "reason": _text(payload.get("reason")),
            "timestamp": payload.get("timestamp", payload.get("done_at")),
        }

    async def _record(self, payload: dict[str, Any], stage: str, count_key: str) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = _text(payload.get("account_id"))
        request_id = _text(payload.get("request_id") or payload.get("command_id"))
        if not account or not request_id:
            self._identity_dropped += 1
            return
        order_key = self._key(account, request_id)
        current = self._orders.get(order_key)
        if current is None or _STAGE_RANK.get(stage, 0) >= _STAGE_RANK.get(str(current.get("stage")), 0):
            self._orders[order_key] = self._summary(payload, stage)
        self._counts[count_key] += 1
        self._account_counts(account)[count_key] += 1
        self._seen += 1
        await self._emit()

    async def _on_built(self, payload: dict[str, Any]) -> None:
        await self._record(payload, STAGE_BUILT, "built")

    async def _on_final(self, payload: dict[str, Any]) -> None:
        await self._record(payload, STAGE_FINALIZED, "decision_finalized")

    async def _on_rejected(self, payload: dict[str, Any]) -> None:
        reason = _text(payload.get("reason"))
        if reason:
            self._reject_reasons[reason] = self._reject_reasons.get(reason, 0) + 1
        await self._record(payload, STAGE_REJECTED, "rejected")

    async def _on_skipped(self, payload: dict[str, Any]) -> None:
        reason = _text(payload.get("reason"))
        if reason:
            self._skip_reasons[reason] = self._skip_reasons.get(reason, 0) + 1
        await self._record(payload, STAGE_SKIPPED, "order_skipped")

    async def _on_bridge_written(self, payload: dict[str, Any]) -> None:
        await self._record(payload, STAGE_QUEUED, "queued_to_bridge")

    async def _on_bridge_failed(self, payload: dict[str, Any]) -> None:
        await self._record(payload, "BRIDGE_WRITE_FAILED", "bridge_write_failed")

    async def _on_ack(self, payload: dict[str, Any]) -> None:
        await self._record(payload, STAGE_ACK, "broker_acknowledged")

    async def _on_command_failed(self, payload: dict[str, Any]) -> None:
        await self._record(payload, STAGE_FAILED, "broker_command_failed")

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        stage = _STAGE_BY_TRADE.get(_text(payload.get("event_type")).upper())
        if stage is None:
            return
        await self._record(payload, stage, stage.lower())

    async def _on_manage_cmd(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = _text(payload.get("account_id"))
        if not account:
            self._identity_dropped += 1
            return
        self._counts["manage_commands"] += 1
        self._account_counts(account)["manage_commands"] += 1
        self._seen += 1
        await self._emit()

    async def _on_manage_written(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = _text(payload.get("account_id"))
        if not account:
            self._identity_dropped += 1
            return
        self._counts["manage_queued"] += 1
        self._account_counts(account)["manage_queued"] += 1
        self._seen += 1
        await self._emit()

    async def _on_outcome(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = _text(payload.get("account_id"))
        if not account:
            self._identity_dropped += 1
            return
        self._last_outcome_by_account[account] = {
            "account_id": account, "symbol": _text(payload.get("symbol")),
            "profit": _number(payload.get("profit")), "result": payload.get("result")}
        self._counts["outcomes"] += 1
        self._account_counts(account)["outcomes"] += 1
        self._seen += 1
        await self._emit()

    async def _on_halt(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = _text(payload.get("account_id"))
        if not account:
            self._identity_dropped += 1
            return
        self._rehydrated = True
        self._halted_accounts[account] = _text(payload.get("reason")) or "RISK_HALT"
        self._counts["halts"] += 1
        self._account_counts(account)["halts"] += 1
        self._seen += 1
        await self._emit()

    async def _on_reset(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = _text(payload.get("account_id"))
        if account:
            self._halted_accounts.pop(account, None)
            self._rehydrated = True
            await self._emit()

    def _state(self) -> dict[str, Any]:
        return {
            "id": "execution_manager", "status": "READ_ONLY",
            "counts": dict(self._counts),
            "counts_by_account": {a: dict(v) for a, v in self._counts_by_account.items()},
            "halted_accounts": dict(self._halted_accounts),
            "orders": {key: dict(value) for key, value in self._orders.items()},
            "last_outcome_by_account": dict(self._last_outcome_by_account),
            "reject_reasons": dict(self._reject_reasons),
            "skip_reasons": dict(self._skip_reasons),
            "vocabulary": "PROVEN_EVENT_BOUNDARIES",
        }

    async def _emit(self) -> None:
        if self._context is not None:
            await self._context.publish(EVENT_OUT, self._state())

    async def _rehydrate(self, payload: dict[str, Any]) -> None:
        if self._rehydrated:
            return
        self._rehydrated = True
        if not isinstance(payload, dict):
            self._restore_error = "RESTORE_FAILED_FAIL_CLOSED"
            return
        try:
            self._load_state(payload)
        except ValueError:
            self._restore_error = "RESTORE_FAILED_FAIL_CLOSED"

    def _load_state(self, state: dict[str, Any]) -> None:
        counts = state.get("counts")
        by_account = state.get("counts_by_account")
        orders = state.get("orders")
        halted = state.get("halted_accounts")
        outcomes = state.get("last_outcome_by_account", {})
        if not all(isinstance(value, dict) for value in (counts, by_account, orders, halted, outcomes)):
            raise ValueError("INVALID_EXECUTION_MANAGER_STATE")
        self._counts = {key: int(counts.get(key, 0)) for key in _COUNT_KEYS}
        self._counts_by_account = {
            str(account): {key: int(row.get(key, 0)) for key in _COUNT_KEYS}
            for account, row in by_account.items() if isinstance(row, dict)}
        self._orders = {str(k): dict(v) for k, v in orders.items() if isinstance(v, dict)}
        self._halted_accounts = {str(k): str(v) for k, v in halted.items()}
        self._last_outcome_by_account = {
            str(k): dict(v) for k, v in outcomes.items() if isinstance(v, dict)}
        reject_reasons = state.get("reject_reasons", {})
        skip_reasons = state.get("skip_reasons", {})
        self._reject_reasons = {str(k): int(v) for k, v in reject_reasons.items()} \
            if isinstance(reject_reasons, dict) else {}
        self._skip_reasons = {str(k): int(v) for k, v in skip_reasons.items()} \
            if isinstance(skip_reasons, dict) else {}
        self._restore_error = ""

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, **self._state()}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_EXECUTION_MANAGER_STATE")
        self._load_state(state)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"counts": dict(self._counts), "accounts": len(self._counts_by_account),
                   "orders": len(self._orders), "halted_accounts": sorted(self._halted_accounts),
                   "reject_reasons": dict(self._reject_reasons),
                   "skip_reasons": dict(self._skip_reasons),
                   "identity_dropped": self._identity_dropped,
                   "restore_error": self._restore_error}
        if self._restore_error or self._identity_dropped:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=self._restore_error or "MISSING_EXECUTION_IDENTITY",
                                details=details)
        if self._seen == 0 and not self._orders:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_FINAL_DECISION_OR_ORDER | finalized=0 queued=0",
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="finalized=%d queued=%d ack=%d fills=%d" % (
                                self._counts["decision_finalized"], self._counts["queued_to_bridge"],
                                self._counts["broker_acknowledged"],
                                self._counts["filled_open"] + self._counts["filled_partial"] + self._counts["filled_closed"]),
                            details=details)
