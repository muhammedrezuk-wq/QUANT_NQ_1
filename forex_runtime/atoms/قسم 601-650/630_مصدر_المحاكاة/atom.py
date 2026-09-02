from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_OUT = "feed.replay.tick"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DB = "REPLAY_DB_NOT_FOUND"
REASON_IDLE = "IDLE"
MAX_SLEEP_S = 5.0
STATE_RUNNING = "RUNNING"
STATE_DONE = "DONE"

# Build 2 (X.md, owner order 2026-08-23): the simulation source. Reads
# market_data.db and publishes each tick exactly as the live feed (622) does
# -- same payload shape, same field names. Downstream (613 -> 112 -> sections
# -> decision) cannot tell the difference. The clock follows the tick's
# occurred_at, not the wall. One atom, zero changes anywhere else.


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._db_path = ""
        self._symbol = ""
        self._speed = 1.0
        self._batch_size = 100
        self._start_at: float | None = None
        self._end_at: float | None = None
        self._task: asyncio.Task | None = None
        self._published = 0
        self._skipped = 0
        self._total_rows = 0
        self._state = REASON_IDLE
        self._last_error = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = str(cfg.get("db_path", "var/store/market_data.db"))
        self._symbol = str(cfg.get("symbol", "")).strip().upper()
        self._speed = _to_float(cfg.get("speed", 1.0))
        if self._speed is None or self._speed < 0:
            self._speed = 1.0
        self._batch_size = max(1, int(cfg.get("batch_size", 100)))
        self._start_at = _to_float(cfg.get("start_at"))
        self._end_at = _to_float(cfg.get("end_at"))
        self._loop = _to_float(cfg.get("loop", 0)) == 1

    async def start(self) -> None:
        self._running = True
        if self._task is None:
            self._task = asyncio.create_task(self._replay_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def shutdown(self) -> None:
        await self.stop()

    def _connect(self) -> sqlite3.Connection:
        path = Path(self._db_path)
        if not path.exists():
            raise FileNotFoundError(self._db_path)
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    async def _replay_loop(self) -> None:
        """Reads ticks from the store and publishes them as feed.replay.tick."""
        if self._context is None:
            return
        try:
            conn = self._connect()
        except (FileNotFoundError, sqlite3.Error) as exc:
            self._last_error = str(exc)
            self._state = REASON_NO_DB
            return

        try:
            where_parts = []
            params: list[Any] = []
            if self._symbol:
                where_parts.append("symbol = ?")
                params.append(self._symbol)
            if self._start_at is not None:
                where_parts.append("occurred_at >= ?")
                params.append(self._start_at)
            if self._end_at is not None:
                where_parts.append("occurred_at <= ?")
                params.append(self._end_at)
            where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
            query = f"SELECT id, symbol, provider, bid, ask, occurred_at, payload_json FROM market_data{where} ORDER BY occurred_at ASC, id ASC"
            cursor = conn.execute(query, params)
            self._state = STATE_RUNNING

            last_occurred: float | None = None
            batch: list[dict[str, Any]] = []

            while self._running:
                rows = cursor.fetchmany(self._batch_size)
                if not rows:
                    if self._loop:
                        cursor = conn.execute(query, params)
                        last_occurred = None
                        continue
                    self._state = STATE_DONE
                    break

                for row in rows:
                    self._total_rows += 1
                    payload = self._decode_payload(row)
                    if payload is None:
                        self._skipped += 1
                        continue

                    occurred = _to_float(row["occurred_at"])
                    if occurred is not None and last_occurred is not None and self._speed > 0:
                        delay = (occurred - last_occurred) / self._speed
                        if delay > 0:
                            await asyncio.sleep(min(delay, MAX_SLEEP_S))

                    await self._context.publish(EVENT_OUT, payload)
                    self._published += 1
                    last_occurred = occurred

        except asyncio.CancelledError:
            pass
        except sqlite3.Error as exc:
            self._last_error = str(exc)
            self._state = f"ERROR:{exc}"
        finally:
            conn.close()

    def _decode_payload(self, row: sqlite3.Row) -> dict[str, Any] | None:
        """Decodes the stored tick into the same shape 622 publishes."""
        raw = row["payload_json"]
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        # Ensure the essential fields exist
        bid = _to_float(row["bid"])
        ask = _to_float(row["ask"])
        if bid is None or ask is None or bid <= 0 or ask < bid:
            return None
        # The replay source replaces provider and stamps the replay identity
        payload["bid"] = bid
        payload["ask"] = ask
        payload["symbol"] = row["symbol"]
        payload["provider"] = "REPLAY"
        payload["occurred_at"] = row["occurred_at"]
        return payload

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "published": self._published,
                "skipped": self._skipped, "state": self._state}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._published = int(state.get("published", 0))
            self._skipped = int(state.get("skipped", 0))
            self._state = str(state.get("state", REASON_IDLE))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"published": self._published, "skipped": self._skipped,
                   "total_rows": self._total_rows, "state": self._state,
                   "speed": self._speed, "symbol": self._symbol or "ALL",
                   "last_error": self._last_error}
        if self._state == REASON_NO_DB:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DB, details=details)
        if self._state == STATE_DONE:
            return HealthStatus(state=HealthState.HEALTHY, message=f"published={self._published} DONE", details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="published=%d skipped=%d state=%s" % (self._published, self._skipped, self._state),
            details=details)
