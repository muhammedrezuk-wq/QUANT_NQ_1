from __future__ import annotations

import asyncio
import os
import sqlite3
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.3.0"

EVENT_PULSE = "SYS_SECOND"
EVENT_NEWS = "market.news"
EVENT_CALENDAR = "market.calendar"

TABLE_NEWS = "news"
TABLE_CALENDAR = "calendar"

# Owner item 22 / batch A (ruling Q7): symbols and impact_level ride along
# only when the bridge table really has those columns. The schema is checked
# at read time and the SELECT is built from named columns that exist — a
# missing column degrades to an absent field, never to a crash and never to
# an invented value.
_NEWS_COLUMNS = ("id", "headline", "link", "source", "sentiment_score",
                 "impact_level", "symbols", "published_at", "written_at")
_SQL_NEWS_TAIL = " FROM news WHERE id > ? ORDER BY id LIMIT ?"
# Owner stamp 2026-08-25: ascending order read the OLDEST rows, and the bridge
# table keeps months of history (measured: 815 rows, 736 past, 79 future). The
# oldest 500 held ZERO future events, so 109 pruned every one of them and the
# economic calendar reached the system empty. Descending takes the newest rows,
# which always contain every future event. No clock is read here -- ordering
# alone fixes it, and rule 13 stays intact.
_SQL_CALENDAR = ("SELECT id, title, country, currency, impact_level, "
                 "scheduled_at, actual, forecast, previous, written_at "
                 "FROM calendar ORDER BY scheduled_at DESC LIMIT ?")

_BUSY_TIMEOUT_MS = 3000
_CONNECT_TIMEOUT_S = 5.0

IMPACT_UNKNOWN = "UNKNOWN"
SYMBOLS_UNKNOWN = "UNKNOWN"
SYMBOL_SEPARATOR = ","

REASON_NOT_STARTED = "NOT_STARTED"
REASON_UNREADABLE = "BRIDGE_UNREADABLE"
REASON_NO_DATA = "NO_ROWS_YET"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_db(configured: str) -> str:
    override = os.environ.get("NQ_BRIDGE_DB", "").strip()
    return override or configured


def _bridge_connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True,
                                 timeout=_CONNECT_TIMEOUT_S)
    connection.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, name: str) -> set[str]:
    rows = connection.execute("PRAGMA table_info(%s)" % name).fetchall()
    return {str(row["name"]) for row in rows}


def _news_select(available: set[str]) -> str | None:
    """SELECT built from the columns the table actually has (named, ordered).

    id is the paging cursor: without it no safe incremental read exists, so
    the read is skipped entirely rather than guessed.
    """
    selected = [column for column in _NEWS_COLUMNS if column in available]
    if "id" not in selected:
        return None
    return "SELECT %s%s" % (", ".join(selected), _SQL_NEWS_TAIL)


def _symbols_of(raw: Any) -> list[str]:
    """Unified names separated by commas, or the literal UNKNOWN.

    Unknown or empty yields an empty list and the caller then omits the
    field: absence of knowledge is not a claim of "no symbols".
    """
    text = str(raw or "").strip()
    if not text or text == SYMBOLS_UNKNOWN:
        return []
    return [part.strip() for part in text.split(SYMBOL_SEPARATOR) if part.strip()]


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._db_path = ""
        self._poll_interval_s = 0.0
        self._batch_limit = 0
        self._reading = False
        self._read_task: asyncio.Task | None = None
        self._now = 0.0
        self._last_read_at = 0.0
        self._last_news_id = 0
        self._seen_events: dict[str, float] = {}
        self._last_error = ""
        self._news_published = 0
        self._calendar_published = 0
        self._reads = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = _resolve_db(str(cfg["db_path"]))
        self._poll_interval_s = float(cfg["poll_interval_s"])
        self._batch_limit = int(cfg["batch_limit"])
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        task = self._read_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._read_task = None
        self._reading = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = _to_float(payload.get("official_time"))
        if now is None:
            return
        self._now = now
        if self._reading or (now - self._last_read_at) < self._poll_interval_s:
            return
        self._last_read_at = now
        self._reading = True
        self._read_task = asyncio.create_task(self._read_all())

    def _read_rows(self) -> tuple[list[dict], list[dict]]:
        connection = _bridge_connect(self._db_path)
        try:
            news: list[dict] = []
            if _table_exists(connection, TABLE_NEWS):
                select = _news_select(_table_columns(connection, TABLE_NEWS))
                if select is not None:
                    news = [dict(r) for r in connection.execute(
                        select, (self._last_news_id, self._batch_limit)).fetchall()]
            calendar: list[dict] = []
            if _table_exists(connection, TABLE_CALENDAR):
                calendar = [dict(r) for r in connection.execute(
                    _SQL_CALENDAR, (self._batch_limit,)).fetchall()]
            return news, calendar
        finally:
            connection.close()

    async def _read_all(self) -> None:
        try:
            if self._context is None:
                return
            try:
                news, calendar = await asyncio.to_thread(self._read_rows)
            except sqlite3.Error as exc:
                self._last_error = str(exc)
                return
            self._last_error = ""
            self._reads += 1
            for row in news:
                await self._emit_news(row)
            for row in calendar:
                await self._emit_calendar(row)
        finally:
            self._reading = False

    async def _emit_news(self, row: dict[str, Any]) -> None:
        if self._context is None:
            return
        row_id = row.get("id")
        if isinstance(row_id, int) and row_id > self._last_news_id:
            self._last_news_id = row_id
        published_at = _to_float(row.get("published_at"))
        body: dict[str, Any] = {
            "id": row_id, "headline": row.get("headline"), "link": row.get("link"),
            "source": row.get("source"),
            "sentiment_score": _to_float(row.get("sentiment_score")),
            "impact_level": row.get("impact_level") or IMPACT_UNKNOWN,
            "published_at": published_at}
        symbols = _symbols_of(row.get("symbols"))
        if symbols:
            body["symbols"] = symbols
        # Owner stamp 2026-08-25: written_at is the moment WE pulled the row,
        # not the moment the news happened. Substituting it for the event time
        # made a three-week-old headline arrive stamped "now", and the
        # high-impact guard is exactly a "did this happen within +/- 15
        # minutes" question. Measured on the bridge table: 20 of 214 rows carry
        # no publish time. When it is unknown the event has NO time: written_at
        # rides along under its own name so nothing is lost and nothing lies.
        written_at = _to_float(row.get("written_at"))
        if written_at is not None:
            body["written_at"] = written_at
        if published_at is not None:
            body["timestamp"] = published_at
        self._news_published += 1
        await self._context.publish(EVENT_NEWS, body)

    async def _emit_calendar(self, row: dict[str, Any]) -> None:
        if self._context is None:
            return
        key = str(row.get("id"))
        written = _to_float(row.get("written_at")) or 0.0
        if self._seen_events.get(key) == written:
            return
        self._seen_events[key] = written
        body: dict[str, Any] = {
            "id": key, "title": row.get("title"), "country": row.get("country"),
            "currency": row.get("currency"),
            "impact_level": row.get("impact_level") or IMPACT_UNKNOWN,
            "scheduled_at": _to_float(row.get("scheduled_at")),
            "actual": row.get("actual"), "forecast": row.get("forecast"),
            "previous": row.get("previous")}
        if written:
            body["timestamp"] = written
        self._calendar_published += 1
        await self._context.publish(EVENT_CALENDAR, body)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"reads": self._reads, "news": self._news_published,
                   "calendar": self._calendar_published,
                   "last_news_id": self._last_news_id,
                   "tracked_events": len(self._seen_events)}
        if self._last_error:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_UNREADABLE,
                                details={**details, "error": self._last_error})
        if self._news_published == 0 and self._calendar_published == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="news=%d calendar=%d" % (
                self._news_published, self._calendar_published),
            details=details)
