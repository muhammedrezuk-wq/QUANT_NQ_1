from __future__ import annotations

import asyncio
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.2"
MIN_RESOLUTION_TIMEOUT_S = 0.1
EVENT_ORDER = "execution.order.requested"
EVENT_REQUEST = "symbol.resolve.requested"
EVENT_REQUEST_OLD = "storage.symbol.resolve_requested"
EVENT_RESULT = "symbol.resolve.result"
EVENT_OUT = "execution.order.resolved"
EVENT_REJECTED = "execution.order.rejected"
EVENT_ORPHAN = "symbol.resolve.orphaned"
EVENT_PULSE = "SYS_SECOND"


def _text(value: Any) -> str:
    return str(value or "").strip()


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context = None
        self._running = False
        self._pending: dict[str, dict[str, Any]] = {}
        self._seen = 0; self._resolved = 0; self._blocked = 0
        self._official_time = 0.0; self._timeout_s = 10.0
        self._expired = 0; self._orphans = 0; self._restored = 0
        self._watchdog_task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._restore_error = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._timeout_s = max(MIN_RESOLUTION_TIMEOUT_S,
                              float(context.config.get("resolution_timeout_s", 10.0)))
        context.subscribe(EVENT_ORDER, self._on_order)
        context.subscribe(EVENT_RESULT, self._on_result)
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._watchdog_task = asyncio.create_task(self._watchdog())
        self._wake.set()

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        task, self._watchdog_task = self._watchdog_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        await self.stop()

    async def _reject(self, order: dict[str, Any], reason: str) -> None:
        self._blocked += 1
        if self._context is not None:
            await self._context.publish(EVENT_REJECTED,
                                        {**order, "reason": reason, "stage": "SYMBOL_RESOLUTION"})

    async def _watchdog(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self._running:
                await self._expire_due()
                deadlines = [float(row["monotonic_deadline"]) for row in self._pending.values()]
                self._wake.clear()
                if not deadlines:
                    await self._wake.wait()
                    continue
                delay = max(0.0, min(deadlines) - loop.time())
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _expire_due(self) -> None:
        now_mono = asyncio.get_running_loop().time()
        expired = []
        for request_id, row in self._pending.items():
            local_due = now_mono >= float(row["monotonic_deadline"])
            official_deadline = row.get("official_deadline")
            official_due = (official_deadline is not None and self._official_time > 0
                            and self._official_time >= float(official_deadline))
            if local_due or official_due:
                expired.append(request_id)
        for request_id in expired:
            row = self._pending.pop(request_id, None)
            if row is not None:
                self._expired += 1
                await self._reject(row["order"], "SYMBOL_RESOLUTION_TIMEOUT")

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        try:
            self._official_time = float(payload.get("official_time"))
        except (TypeError, ValueError):
            return
        loop_now = asyncio.get_running_loop().time()
        for row in self._pending.values():
            if row.get("official_deadline") is None:
                remaining = max(0.0, float(row["monotonic_deadline"]) - loop_now)
                row["official_created_at"] = self._official_time
                row["official_deadline"] = self._official_time + remaining
        await self._expire_due()
        self._wake.set()

    async def _on_order(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        request_id = _text(payload.get("request_id"))
        if not request_id:
            await self._reject(payload, "MISSING_REQUEST_ID")
            return
        if request_id in self._pending:
            await self._reject(payload, "DUPLICATE_REQUEST_ID")
            return
        logical = _text(payload.get("logical_symbol") or payload.get("symbol")).upper()
        account = _text(payload.get("account_id"))
        if not logical or not account:
            await self._reject(payload, "MISSING_ACCOUNT_OR_SYMBOL")
            return
        loop_now = asyncio.get_running_loop().time()
        official_created = self._official_time if self._official_time > 0 else None
        self._pending[request_id] = {
            "order": dict(payload), "account_id": account, "logical_symbol": logical,
            "broker_symbol": payload.get("broker_symbol"),
            "official_created_at": official_created,
            "official_deadline": (official_created + self._timeout_s
                                  if official_created is not None else None),
            "monotonic_deadline": loop_now + self._timeout_s,
        }
        self._seen += 1
        request = {"request_id": request_id, "account_id": account,
                   "logical_symbol": logical, "broker_symbol": payload.get("broker_symbol")}
        await self._context.publish(EVENT_REQUEST, request)
        await self._context.publish(EVENT_REQUEST_OLD, {**request, "symbol": logical})
        self._wake.set()

    async def _on_result(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        request_id = _text(payload.get("request_id"))
        pending = self._pending.get(request_id)
        if pending is None:
            self._orphans += 1
            await self._context.publish(EVENT_ORPHAN, {
                **payload, "request_id": request_id,
                "reason": "UNKNOWN_OR_EXPIRED_REQUEST_ID"})
            return
        account = _text(payload.get("account_id"))
        logical = _text(payload.get("logical_symbol")).upper()
        if account != pending["account_id"] or logical != pending["logical_symbol"]:
            self._orphans += 1
            await self._context.publish(EVENT_ORPHAN, {
                **payload, "request_id": request_id,
                "reason": "RESULT_IDENTITY_MISMATCH"})
            return
        self._pending.pop(request_id, None); self._wake.set()
        order = pending["order"]
        broker_symbol = _text(payload.get("broker_symbol"))
        if (payload.get("approved") is not True or payload.get("status") != "RESOLVED"
                or not broker_symbol or not isinstance(payload.get("spec"), dict)):
            await self._reject(order, "SYMBOL_UNRESOLVED")
            return
        out = dict(order)
        out.update({"logical_symbol": logical, "asset_canonical": payload.get("asset_canonical"),
                    "broker_symbol": broker_symbol, "symbol": broker_symbol,
                    "symbol_resolution_status": "RESOLVED", "symbol_spec": payload["spec"]})
        self._resolved += 1
        await self._context.publish(EVENT_OUT, out)

    async def snapshot(self) -> dict[str, Any]:
        now = asyncio.get_running_loop().time()
        rows = []
        for request_id, row in self._pending.items():
            rows.append({
                "request_id": request_id, "account_id": row["account_id"],
                "logical_symbol": row["logical_symbol"],
                "broker_symbol": row.get("broker_symbol"), "order": dict(row["order"]),
                "official_created_at": row.get("official_created_at"),
                "official_deadline": row.get("official_deadline"),
                "remaining_monotonic_s": max(0.0, float(row["monotonic_deadline"]) - now),
            })
        return {"version": ATOM_VERSION, "pending": rows,
                "official_time": self._official_time}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or not isinstance(state.get("pending"), list):
            self._pending = {}; self._restore_error = "INVALID_SYMBOL_GATE_STATE"
            raise ValueError(self._restore_error)
        now = asyncio.get_running_loop().time(); restored: dict[str, dict[str, Any]] = {}
        for item in state["pending"]:
            if not isinstance(item, dict) or not isinstance(item.get("order"), dict):
                self._restore_error = "INVALID_SYMBOL_GATE_STATE"; raise ValueError(self._restore_error)
            request_id = _text(item.get("request_id")); account = _text(item.get("account_id"))
            logical = _text(item.get("logical_symbol")).upper()
            try:
                remaining = max(0.0, min(self._timeout_s,
                                         float(item.get("remaining_monotonic_s"))))
            except (TypeError, ValueError):
                self._restore_error = "INVALID_SYMBOL_GATE_STATE"; raise ValueError(self._restore_error)
            if not request_id or not account or not logical or request_id in restored:
                self._restore_error = "INVALID_SYMBOL_GATE_STATE"; raise ValueError(self._restore_error)
            restored[request_id] = {
                "order": dict(item["order"]), "account_id": account,
                "logical_symbol": logical, "broker_symbol": item.get("broker_symbol"),
                "official_created_at": item.get("official_created_at"),
                "official_deadline": item.get("official_deadline"),
                "monotonic_deadline": now + remaining,
            }
        self._pending = restored
        try:
            self._official_time = float(state.get("official_time") or 0.0)
        except (TypeError, ValueError):
            self._official_time = 0.0
        self._restored += len(restored); self._restore_error = ""; self._wake.set()

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"seen": self._seen, "resolved": self._resolved,
                   "blocked": self._blocked, "pending": len(self._pending),
                   "expired": self._expired, "orphans": self._orphans,
                   "restored": self._restored, "restore_error": self._restore_error,
                   "watchdog_alive": self._watchdog_task is not None
                                      and not self._watchdog_task.done()}
        if self._restore_error or not details["watchdog_alive"]:
            return HealthStatus(state=HealthState.UNHEALTHY,
                                message=self._restore_error or "TIMEOUT_WATCHDOG_STOPPED",
                                details=details)
        if self._orphans or self._pending:
            return HealthStatus(state=HealthState.DEGRADED,
                                message="PENDING_OR_ORPHANED_RESOLUTION", details=details)
        if not self._seen:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_ORDER_SYMBOL_RESOLVE | resolved=0 blocked=0",
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="resolved=%d blocked=%d" % (self._resolved, self._blocked),
                            details=details)
