from __future__ import annotations

from typing import Any
from clock import PulseGuard

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_truth import EVENT_SHORTAGE, FinancialTruth, bind_truth

ATOM_VERSION = "1.4.0"
FAIL_CLOSED = "RESTORE_FAILED_FAIL_CLOSED"

EVENT_SESSION = "analysis.session.state"
EVENT_ACCOUNT = "platform.account.state"
EVENT_LOSS = "risk.loss_reported"
EVENT_TRADE = "platform.trade_event"
EVENT_DAY = "SYS_DAY"
EVENT_RESET = "risk.kill_switch.reset_requested"

EVENT_OUT = "risk.session_limits.state"
EVENT_HALT_REQUEST = "risk.halt.requested"
ORIGIN = "506"

ID_SESSION = "session_limits"
BREACH_LOSS = "SESSION_LOSS_LIMIT"
BREACH_TRADES = "MAX_SESSION_TRADES"
BREACH_DRAWDOWN = "EQUITY_DRAWDOWN_LIMIT"
STATUS_OK = "ok"

OPENED = "OPENED"
SESSION_UNKNOWN = "UNKNOWN"
_TIME_SESSIONS = frozenset({"asia", "london", "new_york", "overlap", "closed"})

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_ACCOUNT = "NO_ACCOUNT_DATA"

_PERCENT = 100.0
_DP = 4


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._dropped = 0
        self._context: AtomContext | None = None
        self._running = False
        self._max_loss_pct = 0.0
        self._max_trades = 0
        self._max_drawdown_pct = 0.0
        self._session = SESSION_UNKNOWN
        self._books: dict[str, dict[str, Any]] = {}
        self._restore_error = ""
        self._halts = 0
        self._emitted = 0
        self._day_guard = PulseGuard(EVENT_DAY)
        self._truth = FinancialTruth(ORIGIN)

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._max_loss_pct = float(cfg["max_session_loss_pct"])
        self._max_trades = int(cfg["max_session_trades"])
        self._max_drawdown_pct = float(cfg["max_equity_drawdown_pct"])
        context.subscribe(EVENT_SESSION, self._on_session)
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        bind_truth(self, context, self._truth, ("equity",), after=self._on_equity)
        context.subscribe(EVENT_LOSS, self._on_loss)
        context.subscribe(EVENT_TRADE, self._on_trade)
        context.subscribe(EVENT_DAY, self._on_day)
        context.subscribe(EVENT_RESET, self._on_reset)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _book(self, account_id: str) -> dict[str, Any]:
        return self._books.setdefault(account_id, {
            "peak_equity": None, "equity": None, "drawdown_pct": 0.0,
            "session_loss_pct": 0.0, "session_trades": 0, "breaches": set()})

    async def _on_session(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        session = str(payload.get("signal", "")).strip().lower()
        if session not in _TIME_SESSIONS or session == self._session:
            return
        self._session = session
        for book in self._books.values():
            book["session_loss_pct"] = 0.0
            book["session_trades"] = 0
            book["breaches"].discard(BREACH_LOSS)
            book["breaches"].discard(BREACH_TRADES)
        await self._emit_all(_to_float(payload.get("timestamp")))

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account_id = str(payload.get("account_id") or "")
        if not account_id:
            return
        book = self._book(account_id)
        book["broker"] = str(payload.get("broker") or "") or book.get("broker", "")
        if not self._truth.has(account_id, "equity") and self._context is not None:
            await self._context.publish(EVENT_SHORTAGE, self._truth.shortage_body(
                account_id, "equity", broker=book.get("broker", ""),
                detail="506 drawdown guard"))

    async def _on_equity(self, account_id: str) -> None:
        if not self._running:
            return
        equity = self._truth.get(account_id, "equity")
        if equity is None or equity <= 0.0:
            return
        book = self._book(account_id)
        book["equity"] = equity
        peak = book["peak_equity"]
        if peak is None or equity > peak:
            book["peak_equity"] = equity
            book["drawdown_pct"] = 0.0
        else:
            book["drawdown_pct"] = round((peak - equity) / peak * _PERCENT, _DP)
        stamp = None
        if self._max_drawdown_pct > 0.0 and book["drawdown_pct"] >= self._max_drawdown_pct:
            await self._raise(account_id, BREACH_DRAWDOWN, book["drawdown_pct"],
                              self._max_drawdown_pct, stamp)
        await self._emit(account_id, stamp)

    async def _on_loss(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            self._dropped += 1
            return
        if str(payload.get("completeness") or "").upper() != "COMPLETE":
            return
        account_id = payload.get("account_id")
        loss_pct = _to_float(payload.get("loss_pct"))
        if not account_id or loss_pct is None:
            self._dropped += 1
            return
        account_id = str(account_id)
        book = self._book(account_id)
        if loss_pct > 0.0:
            book["session_loss_pct"] = round(book["session_loss_pct"] + loss_pct, _DP)
        stamp = _to_float(payload.get("timestamp"))
        if self._max_loss_pct > 0.0 and book["session_loss_pct"] >= self._max_loss_pct:
            await self._raise(account_id, BREACH_LOSS, book["session_loss_pct"],
                              self._max_loss_pct, stamp)
        await self._emit(account_id, stamp)

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            self._dropped += 1
            return
        if str(payload.get("event_type", "")) != OPENED:
            return
        account_id = payload.get("account_id")
        if not account_id:
            self._dropped += 1
            return
        account_id = str(account_id)
        book = self._book(account_id)
        book["session_trades"] += 1
        stamp = _to_float(payload.get("timestamp"))
        if self._max_trades > 0 and book["session_trades"] >= self._max_trades:
            await self._raise(account_id, BREACH_TRADES, book["session_trades"],
                              self._max_trades, stamp)
        await self._emit(account_id, stamp)

    async def _raise(self, account_id: str, reason: str, value: float, limit: float,
                     stamp: float | None) -> None:
        if self._context is None:
            return
        book = self._book(account_id)
        if reason in book["breaches"]:
            return
        book["breaches"].add(reason)
        self._halts += 1
        body = {"reason": reason, "origin": ORIGIN, "account_id": account_id,
                "value": value, "limit": limit, "session": self._session}
        if stamp is not None:
            body["timestamp"] = stamp
        await self._context.publish(EVENT_HALT_REQUEST, body)

    async def _on_day(self, payload: dict[str, Any]) -> None:
        if not self._running or not self._day_guard.accept(payload):
            return
        for book in self._books.values():
            book["session_loss_pct"] = 0.0
            book["session_trades"] = 0
            book["drawdown_pct"] = 0.0
            book["peak_equity"] = book["equity"]
            book["breaches"] = set()
        await self._emit_all(_to_float(payload.get("timestamp")))

    async def _on_reset(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account_id = str(payload.get("account_id") or "")
        if not account_id or account_id not in self._books:
            return
        self._books[account_id]["breaches"] = set()
        await self._emit(account_id, None)

    async def _emit_all(self, stamp: float | None) -> None:
        for account_id in list(self._books):
            await self._emit(account_id, stamp)

    def _state(self, account_id: str) -> dict[str, Any]:
        book = self._book(account_id)
        return {"account_id": account_id, "id": ID_SESSION, "status": STATUS_OK,
                "session": self._session, "session_loss_pct": book["session_loss_pct"],
                "session_trades": book["session_trades"],
                "max_session_loss_pct": self._max_loss_pct,
                "max_session_trades": self._max_trades,
                "equity": book["equity"], "peak_equity": book["peak_equity"],
                "equity_drawdown_pct": book["drawdown_pct"],
                "max_equity_drawdown_pct": self._max_drawdown_pct,
                "breached": sorted(book["breaches"])}

    async def _emit(self, account_id: str, stamp: float | None) -> None:
        if self._context is None:
            return
        body = self._state(account_id)
        if stamp is not None:
            body["timestamp"] = stamp
        await self._context.publish(EVENT_OUT, body)
        self._emitted += 1

    async def snapshot(self) -> dict:
        return {"version": ATOM_VERSION, "session": str(self._session or ""),
                "day_guard": self._day_guard.snapshot(),
                "books": {str(a): {"session_loss_pct": float(b["session_loss_pct"]),
                                   "session_trades": int(b["session_trades"]),
                                   "drawdown_pct": float(b["drawdown_pct"]),
                                   "breaches": sorted(b["breaches"])}
                          for a, b in self._books.items()}}

    async def restore(self, state: dict) -> None:
        books = state.get("books") if isinstance(state, dict) else None
        ok = isinstance(books, dict) and all(
            isinstance(a, str) and isinstance(b, dict)
            and isinstance(b.get("session_loss_pct"), (int, float))
            and isinstance(b.get("session_trades"), int)
            and isinstance(b.get("drawdown_pct"), (int, float))
            and isinstance(b.get("breaches"), list) for a, b in books.items())
        if not ok:
            self._restore_error = FAIL_CLOSED
            raise ValueError(FAIL_CLOSED)
        self._session = str(state.get("session") or "")
        if state.get("day_guard") is not None: self._day_guard.restore(state["day_guard"])
        for account_id, b in books.items():
            book = self._book(account_id)
            book.update({"session_loss_pct": float(b["session_loss_pct"]),
                         "session_trades": int(b["session_trades"]),
                         "drawdown_pct": float(b["drawdown_pct"]),
                         "breaches": set(b["breaches"])})
        self._restore_error = ""

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._restore_error:
            return HealthStatus(state=HealthState.DEGRADED, message=self._restore_error,
                                details={"restore_error": self._restore_error})
        breached = {a: sorted(b["breaches"]) for a, b in self._books.items()
                    if b["breaches"]}
        details = {"session": self._session, "accounts": len(self._books),
                   "halts": self._halts, "emitted": self._emitted,
                   "books": {a: self._state(a) for a in self._books}}
        if not any(b["equity"] is not None for b in self._books.values()):
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_ACCOUNT,
                                details=details)
        if breached:
            return HealthStatus(state=HealthState.DEGRADED,
                                message="session-limit breached: %s" % ",".join(breached),
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="session=%s accounts=%d" % (self._session, len(self._books)),
                            details=details)
