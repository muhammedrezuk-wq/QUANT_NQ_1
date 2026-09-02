# -*- coding: utf-8 -*-
"""Replay Feed Source (625) — X.md Build 2 (owner seal 2026-08-23).

Reads recorded ticks from ``var/store/market_data.db`` and republishes them on
``feed.replay.tick`` in the exact payload shape 622 produces live. Nothing else
changes: the simulation routes the event through the SAME 613 -> 112 -> sections
-> decision chain, decided by deployment config (613's ``routes`` maps
``feed.replay.tick`` to ``market.tick`` only in simulation runs).

Safety contract:
  * ``startup_mode: manual`` — never auto-starts in a production boot.
  * The session also requires an explicit ``replay.session.start`` command.
  * Publishes only ``feed.replay.tick`` — in a production config (routes lack
    the event) an accidental start reaches no subscriber and is counted.
  * Deterministic identity: every replayed tick is stamped
    ``tick_id = "<prefix>-<row id>"`` BEFORE publish, so the canonical tick
    adapter's identity chain resolves to a stable id (never the bus's random
    event_id) — replaying the same segment twice yields the same cycle ids.
  * No wall-clock reads: timestamps come from the recorded data only.
  * Writes nothing to any store (X.md equivalence condition 4).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_OUT = "feed.replay.tick"
EVENT_STATE = "replay.session.state"
EVENT_START = "replay.session.start"
EVENT_STOP = "replay.session.stop"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SESSION = "NO_SESSION_YET"
REASON_DB_UNREADABLE = "DB_UNREADABLE"

DEFAULT_DB = "var/store/market_data.db"
# Streaming batch size for the read-only cursor -- bounded memory per fetch.
FETCH_BATCH_ROWS = 500


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._db_path = DEFAULT_DB
        self._symbols: list[str] = []
        self._limit = 0
        self._account_default = ""
        self._broker_default = ""
        self._tick_prefix = "replay"
        self._pace_s = 0.0
        self._progress_every = 1000
        self._session_status = REASON_NO_SESSION
        self._rows_read = 0
        self._published = 0
        # X.md Build 3: a dropped input is counted with its reason code.
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}
        self._stop_requested = False
        self._last_error = ""

    # -- lifecycle -----------------------------------------------------------

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = str(cfg.get("db_path") or DEFAULT_DB)
        self._symbols = [str(s) for s in (cfg.get("symbols") or [])]
        self._limit = max(0, int(cfg.get("limit") or 0))
        self._account_default = str(cfg.get("account_id") or "")
        self._broker_default = str(cfg.get("broker") or "")
        self._tick_prefix = str(cfg.get("tick_prefix") or "replay")
        self._pace_s = max(0.0, float(cfg.get("pace_seconds") or 0.0))
        self._progress_every = max(1, int(cfg.get("progress_every") or 1000))
        context.subscribe(EVENT_START, self._on_start)
        context.subscribe(EVENT_STOP, self._on_stop)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._stop_requested = True

    async def shutdown(self) -> None:
        await self.stop()

    # -- session control -----------------------------------------------------

    async def _on_stop(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        self._stop_requested = True

    async def _on_start(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        await self._run_session()

    # -- the replay session --------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        path = Path(self._db_path)
        if not path.is_file():
            raise FileNotFoundError(self._db_path)
        # read-only: the replay feed can never mutate recorded history.
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    async def _run_session(self) -> None:
        assert self._context is not None
        self._stop_requested = False
        self._rows_read = 0
        self._published = 0
        self._dropped = 0
        self._drop_reasons = {}
        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001 - reason is reported, never hidden
            self._last_error = REASON_DB_UNREADABLE
            self._drop(REASON_DB_UNREADABLE)
            self._session_status = REASON_DB_UNREADABLE
            await self._context.publish(EVENT_STATE, {
                "status": "ERROR", "reason": REASON_DB_UNREADABLE, "detail": str(exc)})
            return

        query = "SELECT id, symbol, provider, bid, ask, occurred_at, payload_json FROM market_data"
        clauses: list[str] = []
        params: list[Any] = []
        if self._symbols:
            placeholders = ", ".join("?" for _ in self._symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(self._symbols)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        if self._limit:
            query += " LIMIT ?"
            params.append(self._limit)

        self._session_status = "RUNNING"
        await self._context.publish(EVENT_STATE, {"status": "STARTED",
                                                  "db_path": self._db_path,
                                                  "symbols": list(self._symbols)})
        try:
            cursor = conn.execute(query, params)
            while not self._stop_requested:
                batch = cursor.fetchmany(FETCH_BATCH_ROWS)
                if not batch:
                    break
                for row_id, symbol, provider, bid, ask, occurred_at, payload_json in batch:
                    if self._stop_requested:
                        break
                    self._rows_read += 1
                    tick = self._build_tick(row_id, symbol, provider, bid, ask,
                                            occurred_at, payload_json)
                    if tick is None:
                        continue
                    await self._context.publish(EVENT_OUT, tick)
                    self._published += 1
                    if self._pace_s:
                        import asyncio
                        await asyncio.sleep(self._pace_s)
                    if self._published % self._progress_every == 0:
                        await self._context.publish(EVENT_STATE, {
                            "status": "RUNNING", "rows_read": self._rows_read,
                            "published": self._published})
        except Exception as exc:  # noqa: BLE001 - reported with reason
            self._last_error = "SESSION_ERROR"
            self._drop("SESSION_ERROR")
            self._session_status = "ERROR"
            await self._context.publish(EVENT_STATE, {
                "status": "ERROR", "reason": "SESSION_ERROR", "detail": str(exc)})
            return
        finally:
            conn.close()

        self._session_status = "STOPPED" if self._stop_requested else "DONE"
        await self._context.publish(EVENT_STATE, {
            "status": self._session_status, "rows_read": self._rows_read,
            "published": self._published, "dropped": self._dropped,
            "drop_reasons": dict(self._drop_reasons)})

    def _build_tick(self, row_id: int, symbol: Any, provider: Any, bid: Any,
                    ask: Any, occurred_at: Any, payload_json: Any) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        if payload_json:
            try:
                loaded = json.loads(payload_json)
                if isinstance(loaded, dict):
                    payload = loaded
                else:
                    self._drop("BAD_PAYLOAD_JSON"); return None
            except (ValueError, TypeError):
                self._drop("BAD_PAYLOAD_JSON"); return None
        # Recorded columns are the truth; the JSON fills the rest (622 shape).
        tick: dict[str, Any] = dict(payload)
        tick["symbol"] = str(symbol or tick.get("symbol") or "")
        tick["provider"] = str(provider or tick.get("provider") or "REPLAY")
        if bid is not None:
            tick["bid"] = bid
        if ask is not None:
            tick["ask"] = ask
        if "price" not in tick and bid is not None and ask is not None:
            tick["price"] = (float(bid) + float(ask)) / 2.0
        stamp = occurred_at if occurred_at is not None else payload.get("exchange_timestamp")
        tick["timestamp"] = stamp
        tick["exchange_timestamp"] = stamp
        tick["account_id"] = str(payload.get("account_id") or self._account_default or "")
        tick["broker"] = str(payload.get("broker") or self._broker_default or "")
        # Deterministic identity BEFORE publish: the canonical adapter resolves
        # tick_id first, so the bus's random event_id never leaks into cycle ids.
        tick["tick_id"] = f"{self._tick_prefix}-{row_id}"
        # 613 carries only its identity keys, so the deterministic id rides
        # BOTH channels (tick_id + sequence) — whichever survives, identity
        # never falls through to the bus's random event_id.
        tick["sequence"] = f"{self._tick_prefix}-{row_id}"
        if not tick["symbol"] or not tick["account_id"] or not tick["broker"]:
            self._drop("IDENTITY_MISSING"); return None
        return tick

    # -- health (Build 3: counts and reasons are always visible) -------------

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"status": self._session_status, "rows_read": self._rows_read,
                   "published": self._published, "dropped": self._dropped,
                   "drop_reasons": dict(self._drop_reasons)}
        if self._session_status == REASON_NO_SESSION:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_SESSION, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=f"rows={self._rows_read} published={self._published}",
                            details=details)
