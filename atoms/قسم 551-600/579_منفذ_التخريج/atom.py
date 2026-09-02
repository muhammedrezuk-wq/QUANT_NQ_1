from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from extraction_persistence import restore as restore_state, snapshot as snapshot_state
from extraction_retry import retry as retry_failed
from extraction_positions import update_legs

ATOM_VERSION = "5.0.0"

EVENT_PARTIAL_REQ = "asset.extraction.requested"
EVENT_FULL_REQ = "asset.full_extraction.requested"
EVENT_POSITIONS = "platform.positions.state"
EVENT_TRADE = "platform.trade_event"
EVENT_MANAGE = "execution.manage.command"
EVENT_PENDING = "asset.extraction.execution_requested"
EVENT_DEACTIVATE = "perpetual.asset.deactivate"
EVENT_CONFIRMED = "asset.extraction.confirmed"
EVENT_FAILED = "asset.extraction.failed"
EVENT_PULSE = "SYS_SECOND"
EVENT_CMD_FAILED = "execution.command.failed"
EVENT_REJECTED = "execution.order.rejected"
EVENT_RETRY = "asset.extraction.retry_requested"

STATUS_REQUESTED = "REQUESTED"
STATUS_EXECUTING = "EXECUTING"
STATUS_FAILED = "FAILED"
STATUS_RETRY = "RETRY"
STATUS_SUCCESS = "SUCCESS"
ACTION_PARTIAL = "CLOSE_PARTIAL"
ACTION_CLOSE = "CLOSE"

REASON_NOT_STARTED = "NOT_STARTED"

SEP = "|"
LOT_DP = 8


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _ticket(value: Any) -> str:
    return str(value or "").strip()


def _pending_key(account: Any, ticket: Any) -> str:
    return str(account or "").strip() + SEP + _ticket(ticket)


class Atom(AtomBase):

    def __init__(self) -> None:
        self._dropped = 0
        self._context = None
        self._running = False
        self._lot_step = 0.01
        self._legs: dict[str, list[dict[str, Any]]] = {}
        self._pending_by_ticket: dict[str, dict[str, Any]] = {}
        self._pending_full: dict[str, dict[str, Any]] = {}
        self._partials = 0
        self._fulls = 0
        self._confirmed = 0
        self._failed = 0
        self._skipped = 0
        self._official_time = 0.0
        self._timeout_s = 30.0
        self._max_attempts = 1
        self._failure_ids: set[str] = set()
        self._magic = 20260801

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._lot_step = float(context.config.get("lot_step", 0.01))
        self._timeout_s = max(1.0, float(context.config.get("confirmation_timeout_s", 30.0)))
        self._max_attempts = max(1, int(context.config.get("max_attempts", 1)))
        self._magic = int(context.config.get("magic",20260801))
        context.subscribe(EVENT_POSITIONS, self._on_positions)
        context.subscribe(EVENT_PARTIAL_REQ, self._on_partial)
        context.subscribe(EVENT_FULL_REQ, self._on_full)
        context.subscribe(EVENT_TRADE, self._on_trade)
        context.subscribe(EVENT_PULSE, self._on_pulse)
        context.subscribe(EVENT_CMD_FAILED, self._on_command_failed)
        context.subscribe(EVENT_REJECTED, self._on_command_failed)
        context.subscribe(EVENT_RETRY, self._on_retry)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_positions(self, payload: dict[str, Any]) -> None:
        await update_legs(self, payload)

    def _round_lot(self, volume: float) -> float:
        stepped = round(volume / self._lot_step) * self._lot_step
        return round(stepped, LOT_DP)

    async def _fail_pending(self, pending_key: str, reason: str) -> None:
        pending = self._pending_by_ticket.get(pending_key)
        if pending is None or self._context is None:
            return
        failure_id = "%s:%s:a%s:%s" % (pending.get("extraction_id"), pending_key,
                                           pending.get("attempts"), reason)
        if failure_id in self._failure_ids:
            return
        self._failure_ids.add(failure_id); self._failed += 1
        pending.update({"status": STATUS_FAILED, "failure_reason": reason,
                        "last_attempt_at": self._official_time})
        extraction_id = str(pending.get("extraction_id") or "")
        full = self._pending_full.get(extraction_id)
        if full is not None:
            full["status"] = STATUS_FAILED; full["failure_reason"] = reason
            for other in full.get("expected", set()):
                if other in self._pending_by_ticket:
                    self._pending_by_ticket[other]["status"] = STATUS_FAILED
                    self._pending_by_ticket[other]["failure_reason"] = reason
        await self._context.publish(EVENT_FAILED, {
            **pending, "reason": reason, "failure_id": failure_id,
            "attempts": int(pending.get("attempts") or 0),
            "last_attempt_at": pending.get("last_attempt_at")})

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        try: self._official_time = float(payload.get("official_time"))
        except (TypeError, ValueError): return
        for ticket, pending in list(self._pending_by_ticket.items()):
            created=_number(pending.get("created_at"))
            if created is None or created <= 0:
                pending["created_at"]=self._official_time;continue
            if (pending.get("status") == STATUS_EXECUTING and
                    self._official_time-created > self._timeout_s):
                await self._fail_pending(ticket, "EXTRACTION_CONFIRMATION_TIMEOUT")

    async def _on_command_failed(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "")
        ticket = _ticket(payload.get("ticket"))
        if not account or not ticket:
            return
        key = _pending_key(account, ticket)
        pending = self._pending_by_ticket.get(key)
        if pending is None:
            return
        request_id = str(payload.get("request_id") or "")
        if not request_id or request_id != str(pending.get("request_id") or ""):
            return
        await self._fail_pending(key, str(payload.get("reason") or "EXTRACTION_COMMAND_FAILED"))

    async def _on_retry(self, payload: dict[str, Any]) -> None:
        await retry_failed(self, payload)

    async def _on_partial(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        amount = _number(payload.get("amount"))
        account_id = str(payload.get("account_id") or "")
        symbol = str(payload.get("symbol") or "")
        extraction_id = str(payload.get("extraction_id") or payload.get("request_id") or "")
        if amount is None or amount <= 0 or not account_id or not symbol or not extraction_id:
            return
        key = account_id + SEP + symbol
        winners = [leg for leg in self._legs.get(key, [])
                   if leg["profit"] > 0 and leg["volume"] > 0]
        if not winners:
            self._skipped += 1
            await self._context.publish(EVENT_FAILED, {
                "extraction_id": extraction_id, "request_id": extraction_id,
                "account_id": payload.get("account_id"), "symbol": symbol,
                "status": STATUS_FAILED, "attempts": 0,
                "last_attempt_at": self._official_time,
                "reason": "NO_PROFITABLE_OPEN_LEG"})
            return
        best = max(winners, key=lambda leg: leg["profit"])
        frac = amount / best["profit"] if best["profit"] > 0 else 1.0
        frac = min(1.0, max(0.0, frac))
        close_vol = self._round_lot(best["volume"] * frac)
        if close_vol < self._lot_step:
            close_vol = self._lot_step
        if close_vol > best["volume"]:
            close_vol = self._round_lot(best["volume"])
        if 0.0 < self._round_lot(best["volume"] - close_vol) < self._lot_step:
            close_vol = self._round_lot(best["volume"])
        ticket = _ticket(best["ticket"])
        pending_key = _pending_key(account_id, ticket)
        if pending_key in self._pending_by_ticket:
            self._skipped += 1
            await self._context.publish(EVENT_FAILED, {"extraction_id": extraction_id,
                "request_id":extraction_id,"account_id": payload.get("account_id"), "symbol": symbol,
                "ticket": best["ticket"],"status":STATUS_FAILED,"attempts":0,
                "last_attempt_at":self._official_time,"reason": "TICKET_ALREADY_PENDING"})
            return
        request_id = "%s-%s-a1" % (extraction_id, ticket)
        command = {"account_id": account_id, "magic":self._magic, "request_id": request_id,
                   "extraction_id": extraction_id, "action": ACTION_PARTIAL,
                   "ticket": best["ticket"], "symbol": symbol, "side": best["side"],
                   "volume": close_vol, "origin": "perpetual-extract",
                   "target_amount": amount}
        self._pending_by_ticket[pending_key] = {
            "extraction_id": extraction_id, "request_id": request_id,
            "account_id": account_id, "symbol": symbol, "kind": "partial",
            "requested_amount": amount, "ticket": best["ticket"],
            "status": STATUS_EXECUTING, "attempts": 1,
            "created_at": self._official_time, "last_attempt_at": self._official_time,
            "failure_reason": "", "command": command}
        self._partials += 1
        await self._context.publish(EVENT_PENDING, dict(self._pending_by_ticket[pending_key]))
        await self._context.publish(EVENT_MANAGE, command)

    async def _on_full(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        extraction_id = str(payload.get("extraction_id") or payload.get("request_id") or "")
        account_id = str(payload.get("account_id") or "")
        if not account_id or not symbol or not extraction_id:
            return
        key = account_id + SEP + symbol
        legs = self._legs.get(key, [])
        if not legs:
            self._skipped += 1
            await self._context.publish(EVENT_FAILED, {
                "extraction_id": extraction_id, "request_id": extraction_id,
                "account_id": account_id, "symbol": symbol,
                "status": STATUS_FAILED, "attempts": 0,
                "last_attempt_at": self._official_time, "reason": "NO_OPEN_LEGS"})
            return
        full = {"extraction_id": extraction_id, "account_id": account_id,
                "symbol": symbol, "expected": set(), "profits": [],
                "status": STATUS_EXECUTING, "failure_reason": ""}
        self._pending_full[extraction_id] = full
        for leg in legs:
            ticket = _ticket(leg["ticket"])
            pending_key = _pending_key(account_id, ticket)
            full["expected"].add(pending_key)
            if pending_key in self._pending_by_ticket:
                await self._fail_pending(pending_key, "TICKET_ALREADY_PENDING")
                self._pending_full.pop(extraction_id, None)
                return
            request_id = "%s-%s-a1" % (extraction_id, ticket)
            command = {"account_id": account_id, "magic":self._magic, "request_id": request_id,
                       "extraction_id": extraction_id, "action": ACTION_CLOSE,
                       "ticket": leg["ticket"], "symbol": symbol, "side": leg["side"],
                       "volume": leg["volume"], "origin": "perpetual-full"}
            self._pending_by_ticket[pending_key] = {
                "extraction_id": extraction_id, "request_id": request_id,
                "account_id": account_id, "symbol": symbol, "kind": "full",
                "requested_amount": _number(payload.get("amount", payload.get("target"))),
                "ticket": leg["ticket"], "status": STATUS_EXECUTING, "attempts": 1,
                "created_at": self._official_time, "last_attempt_at": self._official_time,
                "failure_reason": "", "command": command}
            await self._context.publish(EVENT_PENDING, dict(self._pending_by_ticket[pending_key]))
            await self._context.publish(EVENT_MANAGE, command)
        self._fulls += 1

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            self._dropped += 1
            return
        event_type = str(payload.get("event_type") or "").upper()
        if event_type not in ("PARTIAL", "CLOSED"):
            self._dropped += 1
            return
        account = str(payload.get("account_id") or "")
        ticket = _ticket(payload.get("ticket"))
        request_id = str(payload.get("request_id") or "")
        if not account or not ticket or not request_id:
            self._dropped += 1
            return
        pending_key = _pending_key(account, ticket)
        pending = self._pending_by_ticket.get(pending_key)
        if pending is None:
            self._dropped += 1
            return
        if (account != str(pending.get("account_id") or "")
                or str(payload.get("symbol") or "") != str(pending.get("symbol") or "")
                or request_id != str(pending.get("request_id") or "")):
            return
        profit = _number(payload.get("profit"))
        requested = _number(pending.get("requested_amount"))
        actual = profit
        if actual is None or actual <= 0:
            await self._fail_pending(pending_key, "NO_POSITIVE_REALIZED_AMOUNT")
            return
        pending["status"] = STATUS_SUCCESS
        self._pending_by_ticket.pop(pending_key, None)
        extraction_id = str(pending["extraction_id"])
        full = self._pending_full.get(extraction_id)
        if full is not None:
            full["expected"].discard(pending_key)
            full["profits"].append(actual)
            if full["expected"]:
                return
            self._pending_full.pop(extraction_id, None)
            actual_total = sum(full["profits"])
            body = {"extraction_id": extraction_id, "request_id": extraction_id,
                    "account_id": full["account_id"], "symbol": full["symbol"],
                    "kind": "full", "actual_amount": actual_total,
                    "ticket": payload.get("ticket"), "confirmed_by": "platform.trade_event"}
            await self._confirm(body)
            await self._context.publish(EVENT_DEACTIVATE, {
                "account_id": full["account_id"], "symbol": full["symbol"]})
            return
        await self._confirm({
            **pending, "actual_amount": actual, "ticket": payload.get("ticket"),
            "confirmed_by": "platform.trade_event",
        })

    async def _confirm(self, body: dict[str, Any]) -> None:
        self._confirmed += 1
        await self._context.publish(EVENT_CONFIRMED, body)

    async def snapshot(self) -> dict[str, Any]:
        return snapshot_state(self, ATOM_VERSION)

    async def restore(self, state: dict[str, Any]) -> None:
        restore_state(self, state)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        failed = sum(1 for row in self._pending_by_ticket.values()
                     if row.get("status") == STATUS_FAILED)
        details = {"partials": self._partials, "fulls": self._fulls,
                   "confirmed": self._confirmed, "failed": self._failed,
                   "skipped": self._skipped, "pending": len(self._pending_by_ticket),
                   "failed_pending": failed, "max_attempts": self._max_attempts}
        state = HealthState.DEGRADED if self._pending_by_ticket else HealthState.HEALTHY
        message = "EXTRACTION_FAILED_PENDING" if failed else "confirmed=%d pending=%d" % (
            self._confirmed, len(self._pending_by_ticket))
        return HealthStatus(state=state, message=message, details=details)
