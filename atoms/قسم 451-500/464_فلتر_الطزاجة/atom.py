from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"

EVENT_OUT = "decision.filter.freshness.state"
EVENT_PULSE = "SYS_SECOND"
EVENT_TICK = "market.tick.validated"

METHOD_DB = "price_staleness_vs_newest"
METHOD_LIVE = "live_tick_source_age"
ID_FILTER = "freshness_filter"
SIGNAL_PASS = "pass"
SIGNAL_BLOCK = "block"
STATUS_OK = "ok"
QUALITY_GOOD = "good"
QUALITY_LOW = "low"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "NO_READ_YET"
_BUSY_TIMEOUT_MS = 3000
_CONNECT_TIMEOUT_S = 5.0


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _resolve_db(configured: str) -> str:
    override = os.environ.get("NQ_BRIDGE_DB", "").strip()
    return override or configured


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True, timeout=_CONNECT_TIMEOUT_S
    )
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._db_path = ""
        self._table = ""
        self._poll_interval_s = 0.0
        self._threshold_s = 0.0
        self._verdict: dict[tuple[str, str], bool] = {}
        self._read_count = 0
        self._live_ticks = 0
        self._emitted = 0
        self._official_time = 0.0
        self._blocked_now = 0
        self._last_error = ""
        self._last_poll_mono = 0.0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = _resolve_db(str(cfg["db_path"]))
        self._table = str(cfg["table_name"])
        self._poll_interval_s = float(cfg["poll_interval_s"])
        self._threshold_s = float(cfg["stale_threshold_s"])
        context.subscribe(EVENT_PULSE, self._on_pulse)
        context.subscribe(EVENT_TICK, self._on_tick)

    async def start(self) -> None:
        if self._running or self._context is None:
            return
        self._running = True
        await self._check_once()
        self._last_poll_mono = time.monotonic()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        stamp = _to_float(payload.get("official_time"))
        if stamp is not None:
            self._official_time = stamp
        if not self._running:
            return
        now = time.monotonic()
        if now - self._last_poll_mono >= self._poll_interval_s:
            self._last_poll_mono = now
            await self._check_once()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").strip()
        if not symbol:
            return
        account = str(payload.get("account_id") or "*").strip() or "*"
        broker = str(payload.get("broker") or "").strip()
        source_stamp = _to_float(
            payload.get("exchange_timestamp", payload.get("timestamp"))
        )
        now = self._official_time or _to_float(payload.get("received_at")) or time.time()
        age_s = None if source_stamp is None else max(0.0, now - source_stamp)
        passed = age_s is not None and age_s <= self._threshold_s
        self._live_ticks += 1
        self._verdict[(account, symbol)] = passed
        await self._emit(
            symbol, passed, age_s, account=account, broker=broker,
            method=METHOD_LIVE,
        )
        self._refresh_blocked_count()

    def _read(self) -> list[tuple[str, float | None]]:
        conn = _connect(self._db_path)
        try:
            rows = conn.execute(
                f"SELECT symbol, updated_at FROM {self._table}"
            ).fetchall()
            return [(str(row[0]), _to_float(row[1])) for row in rows if row[0]]
        finally:
            conn.close()

    async def _check_once(self) -> None:
        if self._context is None:
            return
        try:
            rows = await asyncio.to_thread(self._read)
        except sqlite3.Error as exc:
            self._last_error = str(exc)
            return
        self._last_error = ""
        stamps = [stamp for _, stamp in rows if stamp is not None]
        if not stamps:
            return
        self._read_count += 1
        newest = max(stamps)
        for symbol, stamp in rows:
            if stamp is None:
                continue
            age_s = max(0.0, newest - stamp)
            passed = age_s <= self._threshold_s
            self._verdict[("*", symbol)] = passed
            await self._emit(symbol, passed, age_s, method=METHOD_DB)
        self._refresh_blocked_count()

    def _refresh_blocked_count(self) -> None:
        self._blocked_now = sum(not passed for passed in self._verdict.values())

    async def _emit(
        self, symbol: str, passed: bool, age_s: float | None = None, *,
        account: str = "*", broker: str = "", method: str = METHOD_DB,
    ) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_OUT, {
            "account_id": account,
            "broker": broker,
            "symbol": symbol,
            "id": ID_FILTER,
            "cycle_id": "",
            "status": STATUS_OK,
            "signal": SIGNAL_PASS if passed else SIGNAL_BLOCK,
            "score": 0,
            "confidence": 1.0 if passed else 0.0,
            "quality": QUALITY_GOOD if passed else QUALITY_LOW,
            "warnings": [],
            "metadata": {
                "method": method,
                "timeframe": "",
                "passed": passed,
                "measured_at": self._official_time or time.time(),
                "age_s": age_s,
                "stale_threshold_s": self._threshold_s,
            },
        })
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {
            "reads": self._read_count,
            "live_ticks": self._live_ticks,
            "emitted": self._emitted,
            "blocked_now": self._blocked_now,
            "blocked_scopes": [
                list(scope) for scope, passed in self._verdict.items() if not passed
            ],
            "last_error": self._last_error,
        }
        if self._read_count == 0 and self._live_ticks == 0:
            return HealthStatus(
                state=HealthState.DEGRADED,
                message=self._last_error or REASON_NO_DATA,
                details=details,
            )
        state = HealthState.DEGRADED if self._last_error else HealthState.HEALTHY
        return HealthStatus(
            state=state,
            message="reads=%d live=%d blocked=%d emitted=%d" % (
                self._read_count, self._live_ticks, self._blocked_now, self._emitted
            ),
            details=details,
        )
