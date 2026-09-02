from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.2.0"
# v1.2.0 (2026-08-27, item 21/27 of the 27-atom review -- same event name,
# two conflicting shapes between 615 and 616, both publishing
# market.news by deliberate design (see this atom's own history, v1.0.0: the owner's own
# ruling that 615 is a parallel path alongside 616, not a replacement --
# 108 stays untouched and the rich contract is not sacrificed for it --
# coexistence is the intended architecture, not the bug). Two real,
# confirmed problems in that coexistence: (1) impact_level had no fallback here (None) while
# 616 always sent the literal "UNKNOWN" -- aligned to 616's explicit
# constant, the more deliberate of the two. (2) "id" was the bridge
# table's own raw row id from EACH atom's OWN independent database --
# 615's row 17 and 616's row 17 are unrelated news items that happen to
# share a number. The only confirmed consumer (108) uses "id" as its
# dedup key (self._seen_keys) -- an id collision across sources silently
# drops a real, distinct headline as a false duplicate. Namespaced with
# the atom id so cross-source collision is structurally impossible.
# Checked but NOT changed: "symbols" looked inconsistent (616 omits when
# empty, this atom always sends it) but narrow's own `resolved` gate
# below already guarantees symbols is non-empty whenever narrow actually
# publishes -- the field-presence rules already converge in practice for
# the one event these two atoms share; the enriched-only symbols_raw
# companion field has no 616 counterpart to conflict with either.
IMPACT_UNKNOWN = "UNKNOWN"

EVENT_PULSE = "SYS_SECOND"
EVENT_NEWS = "market.news"
EVENT_NEWS_ENRICHED = "market.news.enriched"

TABLE_NEWS = "news"

SCOPE_UNRESOLVED = "UNRESOLVED"
SYMBOLS_UNKNOWN = "UNKNOWN"
SYMBOL_SEPARATOR = ","

_SQL_NEWS = (
    "SELECT id, dedupe_key, headline_ar, headline_src, lang_src, translated, "
    "link, source, source_kind, scope, symbols, rule_score, rule_evidence, "
    "rule_version, model_score, model_confidence, model_version, merge_method, "
    "merge_weights, merge_version, sentiment_score, sentiment_state, "
    "impact_level, status, published_at, written_at "
    "FROM news WHERE id > ? ORDER BY id LIMIT ?")

_BUSY_TIMEOUT_MS = 3000
_CONNECT_TIMEOUT_S = 5.0

REASON_NOT_STARTED = "NOT_STARTED"
REASON_SOURCE_ABSENT = "SOURCE_UNAVAILABLE"
REASON_UNREADABLE = "BRIDGE_UNREADABLE"
REASON_NO_DATA = "NO_ROWS_YET"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_db(configured: str) -> str:
    override = os.environ.get("NQ_NEWS_DB", "").strip()
    return override or configured


def _bridge_connect(db_path: str) -> sqlite3.Connection:
    """Read-only by PRAGMA, not by URI.

    The URI form (mode=ro) cannot open a WAL database when its -shm file is
    absent, and the news database is in exactly that state after every clean
    close of its writer. query_only gives the same guarantee — any write
    raises — without that failure mode.
    """
    connection = sqlite3.connect(db_path, timeout=_CONNECT_TIMEOUT_S)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone()
    return row is not None


def _symbols_of(raw: Any) -> list[str]:
    """Stored as our unified names separated by commas, or the literal
    UNKNOWN. UNKNOWN never becomes an empty list: absence of knowledge is
    not absence of symbols."""
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
        self._now = 0.0
        self._last_read_at = 0.0
        self._last_news_id = 0
        self._last_error = ""
        self._source_missing = False
        self._narrow_published = 0
        self._enriched_published = 0
        self._unresolved_held = 0
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
        # The read happens inside the pulse and the pulse waits for it: no
        # detached task, no work between pulses that nobody watches.
        await self._read_all()

    def _read_rows(self) -> list[dict]:
        if not Path(self._db_path).exists():
            self._source_missing = True
            return []
        self._source_missing = False
        connection = _bridge_connect(self._db_path)
        try:
            if not _table_exists(connection, TABLE_NEWS):
                return []
            return [dict(row) for row in connection.execute(
                _SQL_NEWS, (self._last_news_id, self._batch_limit)).fetchall()]
        finally:
            connection.close()

    async def _read_all(self) -> None:
        try:
            if self._context is None:
                return
            try:
                rows = await asyncio.to_thread(self._read_rows)
            except sqlite3.Error as exc:
                self._last_error = str(exc)
                return
            self._last_error = ""
            self._reads += 1
            for row in rows:
                await self._emit(row)
        finally:
            self._reading = False

    async def _emit(self, row: dict[str, Any]) -> None:
        if self._context is None:
            return
        row_id = row.get("id")
        if isinstance(row_id, int) and row_id > self._last_news_id:
            self._last_news_id = row_id
        # v1.2.0: published "id" is namespaced by atom -- 616 reads a
        # separate database with its own independent row-id sequence, and
        # the only confirmed consumer (108) dedups by this field.
        published_id = "615:%s" % row_id if row_id is not None else None

        published_at = _to_float(row.get("published_at"))
        written_at = _to_float(row.get("written_at"))
        # Owner stamp 2026-08-25: "timestamp" is the moment the NEWS happened,
        # and written_at is the moment WE pulled it -- they are not the same
        # clock. Substituting one for the other made a three-week-old headline
        # arrive looking brand new, and the high-impact window is precisely a
        # "did this happen within +/- 15 minutes" question. When the publish
        # time is unknown the event has no time: written_at still rides along
        # under its own honest name, and nothing downstream may read it as the
        # moment of the event.
        stamp = published_at
        symbols = _symbols_of(row.get("symbols"))
        scope = str(row.get("scope") or SCOPE_UNRESOLVED)
        resolved = scope != SCOPE_UNRESOLVED and bool(symbols)

        enriched: dict[str, Any] = {
            "id": published_id,
            "dedupe_key": row.get("dedupe_key"),
            "headline_ar": row.get("headline_ar"),
            "headline_src": row.get("headline_src"),
            "lang_src": row.get("lang_src"),
            "translated": row.get("translated"),
            "link": row.get("link"),
            "source": row.get("source"),
            "source_kind": row.get("source_kind"),
            "scope": scope,
            "symbols": symbols,
            "symbols_raw": row.get("symbols"),
            "rule_score": _to_float(row.get("rule_score")),
            "rule_evidence": row.get("rule_evidence"),
            "rule_version": row.get("rule_version"),
            "model_score": _to_float(row.get("model_score")),
            "model_confidence": _to_float(row.get("model_confidence")),
            "model_version": row.get("model_version"),
            "merge_method": row.get("merge_method"),
            "merge_weights": row.get("merge_weights"),
            "merge_version": row.get("merge_version"),
            "sentiment_score": _to_float(row.get("sentiment_score")),
            "sentiment_state": row.get("sentiment_state"),
            "impact_level": row.get("impact_level") or IMPACT_UNKNOWN,
            "status": row.get("status"),
            "published_at": published_at,
            "written_at": written_at,
            "forwarded": resolved,
        }
        if stamp is not None:
            enriched["timestamp"] = stamp
        self._enriched_published += 1
        await self._context.publish(EVENT_NEWS_ENRICHED, enriched)

        if not resolved:
            # Unresolved scope never enters the analysis path: an empty symbol
            # list downstream would read as "no symbols" instead of "we do not
            # know which". It is held here, visible, until a scope policy exists.
            self._unresolved_held += 1
            return

        narrow: dict[str, Any] = {
            "id": published_id,
            "headline": row.get("headline_src"),
            "link": row.get("link"),
            "source": row.get("source"),
            "symbols": symbols,
            "sentiment_score": _to_float(row.get("sentiment_score")),
            "impact_level": row.get("impact_level") or IMPACT_UNKNOWN,
            "published_at": published_at,
            "written_at": written_at,
        }
        if stamp is not None:
            narrow["timestamp"] = stamp
        self._narrow_published += 1
        await self._context.publish(EVENT_NEWS, narrow)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"reads": self._reads, "narrow": self._narrow_published,
                   "enriched": self._enriched_published,
                   "unresolved_held": self._unresolved_held,
                   "last_news_id": self._last_news_id}
        if self._last_error:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_UNREADABLE,
                                details={**details, "error": self._last_error})
        if self._source_missing:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_SOURCE_ABSENT,
                                details={**details, "db_path": self._db_path})
        if self._enriched_published == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="enriched=%d narrow=%d held=%d" % (
                self._enriched_published, self._narrow_published,
                self._unresolved_held),
            details=details)
