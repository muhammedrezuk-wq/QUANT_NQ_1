from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.1"

EVENT_TRADE = "platform.trade_event"
EVENT_APPEARED = "platform.position.appeared"
EVENT_VANISHED = "platform.position.vanished"
EVENT_REJECTED = "execution.order.rejected"
EVENT_ACK = "execution.command.ack"
EVENT_CMD_FAILED = "execution.command.failed"
EVENT_STATE = "logs.trades.state"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_WRITE_FAILED = "LOG_WRITE_FAILED"

_SIDE_AR = {"BUY": "BUY", "SELL": "SELL"}
_SEEN_ROWS_CAP = 500
_INT_DISPLAY_LIMIT = 10 ** 15


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _num(value: Any) -> str | None:
    f = _to_float(value)
    if f is None:
        return None
    if f == int(f) and abs(f) < _INT_DISPLAY_LIMIT:
        return str(int(f))
    return ("%.6f" % f).rstrip("0").rstrip(".")


def _signed(value: Any) -> str | None:
    f = _to_float(value)
    if f is None:
        return None
    text = _num(f) or "0"
    return text if f < 0 else "+" + text


def _side(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    return _SIDE_AR.get(raw, raw)


def _text(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw or None


def _join(*parts: str | None) -> str:
    return " · ".join(p for p in parts if p)


def _pair(*parts: str | None) -> str | None:
    joined = " ".join(p for p in parts if p)
    return joined or None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._dir = Path("var/logs")
        self._prefix = "trades"
        self._state_tail = 12
        self._max_lines_per_day = 20000
        self._file_lock = threading.Lock()
        self._day = ""
        self._lines_today = 0
        self._total_lines = 0
        self._suppressed_today = 0
        self._cap_announced = False
        self._io_failures = 0
        self._last_io_error = ""
        self._last_lines: deque[str] = deque(maxlen=12)
        self._seen_rows: OrderedDict[int, bool] = OrderedDict()
        self.events_in = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._dir = Path(str(cfg["dir"]))
        self._prefix = str(cfg["file_prefix"])
        self._state_tail = int(cfg["state_tail"])
        self._max_lines_per_day = int(cfg["max_lines_per_day"])
        self._last_lines = deque(self._last_lines, maxlen=self._state_tail)
        self._dir.mkdir(parents=True, exist_ok=True)
        context.subscribe(EVENT_TRADE, self._on_trade)
        context.subscribe(EVENT_APPEARED, self._on_appeared)
        context.subscribe(EVENT_VANISHED, self._on_vanished)
        context.subscribe(EVENT_REJECTED, self._on_rejected)
        context.subscribe(EVENT_ACK, self._on_ack)
        context.subscribe(EVENT_CMD_FAILED, self._on_cmd_failed)

    async def start(self) -> None:
        if self._running or self._context is None:
            return
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _today_path(self, day: str) -> Path:
        return self._dir / f"{self._prefix}-{day}.log"

    @staticmethod
    def _stamp(payload: dict[str, Any]) -> float:
        stamp = _to_float(payload.get("timestamp")) if isinstance(payload, dict) else None
        return stamp if stamp is not None else time.time()

    def _append(self, day: str, text: str) -> None:
        with self._file_lock:
            path = self._today_path(day)
            fresh = not path.exists()
            with open(path, "a", encoding="utf-8") as fh:
                if fresh:
                    fh.write("- ASMAR TRADE LOG - day %s-%s-%s - all times machine-local -\n"
                             % (day[0:4], day[4:6], day[6:8]))
                fh.write(text + "\n")

    async def _write(self, kind: str, when: float, body: str) -> None:
        if self._context is None:
            return
        day = time.strftime("%Y%m%d")
        if day != self._day:
            self._day = day
            self._lines_today = 0
            self._suppressed_today = 0
            self._cap_announced = False
        if self._lines_today >= self._max_lines_per_day:
            self._suppressed_today += 1
            if not self._cap_announced:
                self._cap_announced = True
                try:
                    await asyncio.to_thread(
                        self._append, day,
                        "⛔ TRADE_LOG_DAILY_CAP_REACHED (%d lines) - remaining events today "
                        "are counted, not written (suppressed in atom 720 health details)"
                        % self._max_lines_per_day)
                except OSError:
                    pass
            return
        line = "%s | %s" % (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when)), body)
        try:
            await asyncio.to_thread(self._append, day, line)
        except OSError as exc:
            self._io_failures += 1
            self._last_io_error = str(exc)
            return
        self._last_io_error = ""
        self._lines_today += 1
        self._total_lines += 1
        self._last_lines.append(line)
        await self._context.publish(EVENT_STATE, {
            "file": str(self._today_path(day)),
            "date": day,
            "kind": kind,
            "lines_today": self._lines_today,
            "total_lines": self._total_lines,
            "last_lines": list(self._last_lines),
            "timestamp": when})

    def _remember_row(self, row_id: int) -> bool:
        seen = row_id in self._seen_rows
        self._seen_rows[row_id] = True
        self._seen_rows.move_to_end(row_id)
        while len(self._seen_rows) > _SEEN_ROWS_CAP:
            self._seen_rows.popitem(last=False)
        return seen

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self.events_in += 1
        kind = str(payload.get("event_type") or "").strip().upper()
        row_id = payload.get("source_row_id")
        revision = isinstance(row_id, int) and self._remember_row(row_id)
        ticket = _text(payload.get("ticket"))
        account = _text(payload.get("account_id"))
        symbol = _text(payload.get("symbol"))
        side = _side(payload.get("side"))
        volume = _num(payload.get("volume"))
        if revision:
            body = _join(
                "TRADE_COSTS_COMPLETED",
                f"ticket {ticket}" if ticket else None,
                f"commission {_num(payload.get('commission'))}" if _num(payload.get("commission")) is not None else None,
                f"swap {_num(payload.get('swap'))}" if _num(payload.get("swap")) is not None else None,
                f"fee {_num(payload.get('fee'))}" if _num(payload.get("fee")) is not None else None,
                f"profit {_signed(payload.get('profit'))}" if _signed(payload.get("profit")) is not None else None)
            await self._write("costs", self._stamp(payload), body)
            return
        head = {"OPENED": "TRADE_OPENED:", "CLOSED": "TRADE_CLOSED:",
                "PARTIAL": "PARTIAL_CLOSE:"}.get(kind, "TRADE_EVENT (%s):" % (kind or "?"))
        entry = _num(payload.get("entry_price"))
        exit_price = _num(payload.get("exit_price"))
        profit = _signed(payload.get("profit"))
        reason = _text(payload.get("reason"))
        if kind == "OPENED":
            detail = _join(f"volume {volume}" if volume else None,
                           f"@ {entry}" if entry else None)
        else:
            detail = _join(f"volume {volume}" if volume else None,
                           f"entry {entry}" if entry else None,
                           f"exit {exit_price}" if exit_price else None,
                           f"profit {profit}" if profit is not None else None)
        body = _join(
            "%s %s" % (head, _pair(side, symbol) or "?"),
            detail or None,
            f"ticket {ticket}" if ticket else None,
            f"account {account}" if account else None,
            f"reason: {reason}" if reason and kind != "OPENED" else None)
        await self._write(kind.lower() or "trade", self._stamp(payload), body)

    async def _position_line(self, payload: dict[str, Any], head: str,
                             kind: str, with_profit: bool) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self.events_in += 1
        body = _join(
            "%s %s" % (head, _pair(_side(payload.get("side")),
                                   _text(payload.get("symbol"))) or "?"),
            f"volume {_num(payload.get('volume'))}" if _num(payload.get("volume")) else None,
            f"@ {_num(payload.get('entry_price'))}" if not with_profit and _num(payload.get("entry_price")) else None,
            f"last floating profit {_signed(payload.get('profit'))}" if with_profit and _signed(payload.get("profit")) is not None else None,
            f"ticket {_text(payload.get('ticket'))}" if _text(payload.get("ticket")) else None,
            f"account {_text(payload.get('account_id'))}" if _text(payload.get("account_id")) else None)
        await self._write(kind, self._stamp(payload), body)

    async def _on_appeared(self, payload: dict[str, Any]) -> None:
        await self._position_line(payload, "POSITION_APPEARED_ON_PLATFORM:", "appeared", False)

    async def _on_vanished(self, payload: dict[str, Any]) -> None:
        await self._position_line(payload, "POSITION_VANISHED_FROM_PLATFORM:", "vanished", True)

    async def _on_rejected(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self.events_in += 1
        body = _join(
            "ORDER_REJECTED: %s" % (_pair(_side(payload.get("side")),
                                    _text(payload.get("symbol"))) or "?"),
            f"volume {_num(payload.get('volume'))}" if _num(payload.get("volume")) else None,
            f"reason: {_text(payload.get('reason')) or '?'}",
            f"request {_text(payload.get('request_id'))}" if _text(payload.get("request_id")) else None,
            f"account {_text(payload.get('account_id'))}" if _text(payload.get("account_id")) else None)
        await self._write("rejected", self._stamp(payload), body)

    async def _result_line(self, payload: dict[str, Any], verdict: str, kind: str) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self.events_in += 1
        status = _text(payload.get("status"))
        reason = _text(payload.get("reason"))
        body = _join(
            "ORDER_RESULT: %s%s" % (verdict, " (%s)" % status if status else ""),
            _text(payload.get("action")),
            _pair(_side(payload.get("side")), _text(payload.get("symbol"))),
            f"volume {_num(payload.get('volume'))}" if _num(payload.get("volume")) else None,
            f"ticket {_text(payload.get('ticket'))}" if _text(payload.get("ticket")) else None,
            f"reason: {reason}" if reason else None,
            f"request {_text(payload.get('request_id'))}" if _text(payload.get("request_id")) else None,
            f"account {_text(payload.get('account_id'))}" if _text(payload.get("account_id")) else None)
        await self._write(kind, self._stamp(payload), body)

    async def _on_ack(self, payload: dict[str, Any]) -> None:
        await self._result_line(payload, "EXECUTED_ON_PLATFORM", "ack")

    async def _on_cmd_failed(self, payload: dict[str, Any]) -> None:
        await self._result_line(payload, "FAILED", "failed")

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        day = time.strftime("%Y%m%d")
        details: dict[str, Any] = {
            "file": str(self._today_path(day)),
            "events_in": self.events_in,
            "lines_today": self._lines_today if day == self._day else 0,
            "suppressed_today": self._suppressed_today if day == self._day else 0,
            "total_lines": self._total_lines,
            "io_failures": self._io_failures,
            "last_io_error": self._last_io_error,
            "last_lines": list(self._last_lines)}
        if self._last_io_error:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_WRITE_FAILED, details=details)
        if self._total_lines == 0:
            return HealthStatus(
                state=HealthState.HEALTHY,
                message="READY_AWAITING_FIRST_REAL_TRADE_OR_ORDER_EVENT | lines=0",
                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="today=%d total=%d" % (details["lines_today"], self._total_lines),
            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"day": self._day, "lines_today": self._lines_today,
                "total_lines": self._total_lines,
                "suppressed_today": self._suppressed_today,
                "last_lines": list(self._last_lines),
                "seen_rows": list(self._seen_rows)[-_SEEN_ROWS_CAP:],
                "events_in": self.events_in}

    async def restore(self, state: dict[str, Any]) -> None:
        day = str(state.get("day") or "")
        today = time.strftime("%Y%m%d")
        self._total_lines = int(state.get("total_lines", 0))
        self.events_in = int(state.get("events_in", 0))
        if day == today:
            self._day = day
            self._lines_today = int(state.get("lines_today", 0))
            self._suppressed_today = int(state.get("suppressed_today", 0))
        for line in list(state.get("last_lines") or [])[-self._state_tail:]:
            self._last_lines.append(str(line))
        for row_id in state.get("seen_rows") or []:
            if isinstance(row_id, int):
                self._seen_rows[row_id] = True
