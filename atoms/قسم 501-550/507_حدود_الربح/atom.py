from __future__ import annotations

from typing import Any
from clock import PulseGuard

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_truth import EVENT_SHORTAGE, FinancialTruth, bind_truth

ATOM_VERSION = "1.3.0"
FAIL_CLOSED = "RESTORE_FAILED_FAIL_CLOSED"

EVENT_OUTCOME = "risk.loss_reported"
EVENT_ACCOUNT = "platform.account.state"
EVENT_DAY = "SYS_DAY"
EVENT_RESET = "risk.kill_switch.reset_requested"

EVENT_OUT = "risk.profit_limits.state"
EVENT_HALT_REQUEST = "risk.halt.requested"
ORIGIN = "507"

ID_PROFIT = "profit_limits"
BREACH_TARGET = "DAILY_PROFIT_TARGET"
BREACH_GIVEBACK = "PROFIT_GIVEBACK"
STATUS_OK = "ok"

RESULT_WIN = "WIN"
RESULT_LOSS = "LOSS"

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
        self._target_pct = 0.0
        self._giveback_pct = 0.0
        self._books: dict[str, dict[str, Any]] = {}
        self._restore_error = ""
        self._seen = 0
        self._emitted = 0
        self._halts = 0
        self._day_guard = PulseGuard(EVENT_DAY)
        self._truth = FinancialTruth(ORIGIN)

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._target_pct = float(cfg["daily_profit_target_pct"])
        self._giveback_pct = float(cfg["max_profit_giveback_pct"])
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        bind_truth(self, context, self._truth, ("equity",), after=self._on_equity)
        context.subscribe(EVENT_OUTCOME, self._on_outcome)
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
            "profit": 0.0, "peak_pct": 0.0, "equity": None,
            "wins": 0, "losses": 0, "breaches": set()})

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        account_id = str(payload.get("account_id") or "")
        if not account_id:
            return
        self._book(account_id)["broker"] = str(payload.get("broker") or "")
        if not self._truth.has(account_id, "equity") and self._context is not None:
            await self._context.publish(EVENT_SHORTAGE, self._truth.shortage_body(
                account_id, "equity", detail="507 profit limits"))

    async def _on_equity(self, account_id: str) -> None:
        if self._running:
            self._book(account_id)

    def _pct(self, book: dict[str, Any], account_id: str = "") -> float:
        equity = self._truth.get(account_id, "equity") if account_id else None
        if equity is None:
            equity = book.get("equity")
        if not equity or equity <= 0.0:
            return 0.0
        return round(book["profit"] / equity * _PERCENT, _DP)

    async def _on_outcome(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            self._dropped += 1
            return
        if str(payload.get("completeness") or "").upper() != "COMPLETE":
            return
        account_id = payload.get("account_id")
        profit = _to_float(payload.get("pnl"))
        if not account_id or profit is None:
            self._dropped += 1
            return
        account_id = str(account_id)
        book = self._book(account_id)
        self._seen += 1
        book["profit"] = round(book["profit"] + profit, _DP)
        if profit > 0:
            book["wins"] += 1
        elif profit < 0:
            book["losses"] += 1
        pct = self._pct(book, account_id)
        if pct > book["peak_pct"]:
            book["peak_pct"] = pct
        stamp = _to_float(payload.get("timestamp"))
        await self._check(account_id, stamp)
        await self._emit(account_id, stamp)

    async def _check(self, account_id: str, stamp: float | None) -> None:
        book = self._book(account_id)
        pct = self._pct(book, account_id)
        if self._target_pct > 0.0 and pct >= self._target_pct:
            await self._raise(account_id, BREACH_TARGET, pct, self._target_pct, stamp)
        given = round(book["peak_pct"] - pct, _DP)
        if self._giveback_pct > 0.0 and book["peak_pct"] > 0.0 and given >= self._giveback_pct:
            await self._raise(account_id, BREACH_GIVEBACK, given, self._giveback_pct, stamp)

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
                "value": value, "limit": limit}
        if stamp is not None:
            body["timestamp"] = stamp
        await self._context.publish(EVENT_HALT_REQUEST, body)

    async def _on_day(self, payload: dict[str, Any]) -> None:
        if not self._running or not self._day_guard.accept(payload):
            return
        for book in self._books.values():
            book["profit"] = 0.0
            book["peak_pct"] = 0.0
            book["wins"] = 0
            book["losses"] = 0
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
        pct = self._pct(book, account_id)
        return {"account_id": account_id, "id": ID_PROFIT, "status": STATUS_OK,
                "daily_profit": book["profit"], "daily_profit_pct": pct,
                "peak_profit_pct": book["peak_pct"],
                "giveback_pct": round(max(0.0, book["peak_pct"] - pct), _DP),
                "daily_profit_target_pct": self._target_pct,
                "max_profit_giveback_pct": self._giveback_pct,
                "equity": book["equity"], "wins": book["wins"], "losses": book["losses"],
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
        return {"version": ATOM_VERSION, "day_guard": self._day_guard.snapshot(),
                "books": {str(a): {"profit": float(b["profit"]), "peak_pct": float(b["peak_pct"]),
                                   "wins": int(b["wins"]), "losses": int(b["losses"]),
                                   "breaches": sorted(b["breaches"])}
                          for a, b in self._books.items()}}

    async def restore(self, state: dict) -> None:
        books = state.get("books") if isinstance(state, dict) else None
        ok = isinstance(books, dict) and all(
            isinstance(a, str) and isinstance(b, dict)
            and isinstance(b.get("profit"), (int, float))
            and isinstance(b.get("wins"), int) and isinstance(b.get("losses"), int)
            and isinstance(b.get("breaches"), list) for a, b in books.items())
        if not ok:
            self._restore_error = FAIL_CLOSED
            raise ValueError(FAIL_CLOSED)
        if state.get("day_guard") is not None: self._day_guard.restore(state["day_guard"])
        for account_id, b in books.items():
            book = self._book(account_id)
            book.update({"profit": float(b["profit"]), "peak_pct": float(b.get("peak_pct") or 0.0),
                         "wins": int(b["wins"]), "losses": int(b["losses"]),
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
        details = {"accounts": len(self._books), "emitted": self._emitted,
                   "halts": self._halts,
                   "books": {a: self._state(a) for a in self._books}}
        if not self._books:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_ACCOUNT,
                                details=details)
        if breached:
            return HealthStatus(state=HealthState.DEGRADED,
                                message="profit-limit breached: %s" % ",".join(breached),
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="accounts=%d emitted=%d" % (len(self._books), self._emitted),
                            details=details)
