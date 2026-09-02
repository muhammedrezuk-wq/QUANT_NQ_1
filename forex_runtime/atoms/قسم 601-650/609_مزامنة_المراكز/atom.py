from __future__ import annotations

import asyncio
import os
import sqlite3
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "5.0.2"
EVENT_OUT = "platform.positions.state"
EVENT_OPENED = "platform.position.appeared"
EVENT_CLOSED = "platform.position.vanished"
EVENT_ACCOUNT = "platform.account.state"
EVENT_PULSE = "SYS_SECOND"
EVENT_SCHEMA = "platform.positions.schema_state"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "POSITIONS_UNKNOWN"
REASON_STALE = "POSITIONS_STALE"
_COLUMNS = ("ticket", "symbol", "side", "volume", "entry_price", "current_price",
            "stop_loss", "take_profit", "profit", "swap", "magic", "opened_at", "updated_at")
_OPTIONAL_FINANCIAL = ("account_id", "commission")
_BUSY_TIMEOUT_MS = 3000
_CONNECT_TIMEOUT_S = 5.0
_VOL_DP = 8
_PNL_DP = 6


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _resolve_db(configured: str) -> str:
    return os.environ.get("NQ_BRIDGE_DB", "").strip() or configured


def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                                 timeout=_CONNECT_TIMEOUT_S)
    connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return connection


def _position_key(row: dict[str, Any]) -> str:
    return "%s|%s" % (str(row.get("account_id") or "?"), str(row.get("ticket") or ""))


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._db_path = ""
        self._table = ""
        self._poll_interval_s = 0.0
        self._stale_after_s = 10.0
        self._positions: dict[str, dict[str, Any]] = {}
        self._accounts: dict[str, str] = {}
        self._last_read_at: float | None = None
        self._official_time: float | None = None
        self._last_error = ""
        self._field_errors: set[str] = set()
        self._schema_missing: set[str] = set()
        self._schema_announced: set[str] = set()
        self._picture_received = False
        self._last_snapshot_status = "UNKNOWN"
        self.read_count = 0
        self.appeared_count = 0
        self.vanished_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._db_path = _resolve_db(str(cfg["db_path"]))
        self._table = str(cfg["table_name"])
        if self._table!="positions_v2":
            self._table="positions_v2";self._last_error="LEGACY_POSITIONS_TABLE_FORBIDDEN"
        self._poll_interval_s = float(cfg["poll_interval_s"])
        self._stale_after_s = float(cfg.get("stale_after_s", 10.0))
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        if account and broker:
            self._accounts[account] = broker

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        stamp = _to_float(payload.get("official_time")) if isinstance(payload, dict) else None
        if stamp is not None:
            self._official_time = stamp

    async def start(self) -> None:
        if self._running or self._context is None:
            return
        self._running = True
        await self._read_once()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def shutdown(self) -> None:
        await self.stop()

    def _query_optional(self, connection: sqlite3.Connection, column: str,
                        tickets: list[Any]) -> dict[Any, Any]:
        if not tickets:
            return {}
        rows = connection.execute(
            f"SELECT ticket, {column} FROM {self._table}").fetchall()
        return {row[0]: row[1] for row in rows}

    def _read_rows(self) -> list[dict[str, Any]]:
        connection = _connect(self._db_path)
        try:
            connection.row_factory = sqlite3.Row
            available = {str(row[1]) for row in connection.execute(
                f"PRAGMA table_info({self._table})").fetchall()}
            rows = [dict(row) for row in connection.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM {self._table}").fetchall()]
            tickets = [row.get("ticket") for row in rows]
            self._field_errors = set()
            self._schema_missing = {name for name in _OPTIONAL_FINANCIAL
                                    if name not in available}
            for name in _OPTIONAL_FINANCIAL:
                values: dict[Any, Any] = {}
                status = "SCHEMA_UNAVAILABLE" if name in self._schema_missing else "AVAILABLE"
                if name not in self._schema_missing:
                    try:
                        values = self._query_optional(connection, name, tickets)
                    except sqlite3.Error:
                        status = "UNAVAILABLE"
                        self._field_errors.add(name)
                for row in rows:
                    value = values.get(row.get("ticket"))
                    row[name] = value
                    row[name + "_status"] = status if value is not None else (
                        "MISSING_VALUE" if status == "AVAILABLE" else status)
            return rows
        finally:
            connection.close()

    def _now(self) -> float:
        return self._official_time if self._official_time is not None else clock.now()

    def _picture_meta(self, rows: list[dict[str, Any]], newest: float | None) -> dict[str, Any]:
        missing: set[str] = set()
        for row in rows:
            if row.get("account_id_status") != "AVAILABLE":
                missing.add("account_id")
            if row.get("commission_status") != "AVAILABLE":
                missing.add("commission")
            if row.get("account_id") and not row.get("broker"):
                missing.add("broker")
        age = self._now() - newest if newest is not None else (0.0 if not rows else None)
        stale = age is None or age < 0 or age > self._stale_after_s
        status = "STALE" if stale else "INCOMPLETE" if missing else "READY"
        return {"snapshot_status": status, "missing_components": sorted(missing),
                "age_s": age, "stale_after_s": self._stale_after_s,
                "usable_for_new_exposure": status == "READY",
                "usable_for_protection": status in {"READY", "STALE"} or
                                         ("account_id" not in missing and "broker" not in missing),
                "complete": not missing}

    async def _announce_schema(self) -> None:
        if self._context is None:
            return
        for field in sorted(self._schema_missing - self._schema_announced):
            self._schema_announced.add(field)
            await self._context.publish(EVENT_SCHEMA, {
                "field": field, "status": "SCHEMA_UNAVAILABLE",
                "reason": field.upper() + "_COLUMN_MISSING", "announced_once": True})

    async def _read_once(self) -> None:
        if self._context is None:
            return
        try:
            rows = await asyncio.to_thread(self._read_rows)
        except sqlite3.Error as exc:
            self._last_error = str(exc)
            return
        self._last_error = ""
        self.read_count += 1
        await self._announce_schema()
        fresh: dict[str, dict[str, Any]] = {}
        newest: float | None = None
        for row in rows:
            ticket = row.get("ticket")
            if ticket is None:
                continue
            updated = _to_float(row.get("updated_at"))
            if updated is not None and (newest is None or updated > newest):
                newest = updated
            account = str(row.get("account_id") or "")
            item = {"ticket": ticket, "symbol": row.get("symbol"), "side": row.get("side"),
                    "volume": _to_float(row.get("volume")),
                    "entry_price": _to_float(row.get("entry_price")),
                    "current_price": _to_float(row.get("current_price")),
                    "stop_loss": _to_float(row.get("stop_loss")),
                    "take_profit": _to_float(row.get("take_profit")),
                    "profit": _to_float(row.get("profit")), "swap": _to_float(row.get("swap")),
                    "commission": _to_float(row.get("commission")), "magic": row.get("magic"),
                    "opened_at": _to_float(row.get("opened_at")), "updated_at": updated,
                    "account_id": account or None, "broker": self._accounts.get(account),
                    "account_id_status": row.get("account_id_status"),
                    "commission_status": row.get("commission_status")}
            fresh[_position_key(item)] = item
        if not rows and self._accounts:
            newest = self._now()
        appeared = [key for key in fresh if key not in self._positions]
        vanished = [key for key in self._positions if key not in fresh]
        previous = self._positions
        self._positions = fresh
        self._last_read_at = newest
        self._picture_received = bool(rows or self._accounts)
        meta = self._picture_meta(list(fresh.values()), newest)
        self._last_snapshot_status = meta["snapshot_status"] if self._picture_received else "UNKNOWN"
        stamp = {"timestamp": newest} if newest is not None else {}
        for key in appeared:
            self.appeared_count += 1
            await self._context.publish(EVENT_OPENED, {**fresh[key], **meta, **stamp})
        for key in vanished:
            self.vanished_count += 1
            await self._context.publish(EVENT_CLOSED, {**previous[key], **meta, **stamp})
        unknown = [dict(row) for row in fresh.values() if not row.get("account_id")]
        accounts = set(self._accounts) | {str(row.get("account_id")) for row in fresh.values()
                                        if row.get("account_id")}
        for account in sorted(accounts):
            owned = [dict(row) for row in fresh.values() if row.get("account_id") == account]
            by_symbol: dict[str, int] = {}
            for row in owned:
                sym = str(row.get("symbol") or "")
                if sym:
                    by_symbol[sym] = by_symbol.get(sym, 0) + 1
            await self._context.publish(EVENT_OUT, {
                "account_id": account, "broker": self._accounts.get(account),
                "positions": owned, "by_symbol": by_symbol,
                "unknown_positions": unknown,
                "unknown_position_count": len(unknown), "open_count": len(owned),
                "source": "609", **meta, **stamp})

    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._poll_interval_s)
                if self._running:
                    await self._read_once()
        except asyncio.CancelledError:
            pass

    def state(self) -> dict[str, Any]:
        by_symbol: dict[str, int] = {}; total_volume = 0.0; floating = 0.0
        for entry in self._positions.values():
            symbol = str(entry.get("symbol")); by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
            if entry.get("volume") is not None: total_volume += abs(entry["volume"])
            if entry.get("profit") is not None: floating += entry["profit"]
        age = self._now()-self._last_read_at if self._last_read_at is not None else None
        return {"open_count": len(self._positions), "tickets": sorted(self._positions),
                "by_symbol": by_symbol, "total_volume": round(total_volume, _VOL_DP),
                "floating_pnl": round(floating, _PNL_DP),
                "positions": [dict(value) for value in self._positions.values()],
                "read_at": self._last_read_at, "age_s": age,
                "snapshot_status": self._last_snapshot_status}

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {**self.state(), "reads": self.read_count, "last_error": self._last_error,
                   "field_errors": sorted(self._field_errors),
                   "schema_missing": sorted(self._schema_missing)}
        if self._last_error:
            return HealthStatus(state=HealthState.DEGRADED,
                                message="POSITION_READ_FAILED", details=details)
        if not self._picture_received:
            return HealthStatus(state=HealthState.UNKNOWN, message=REASON_NO_DATA, details=details)
        if self._field_errors:
            reason = ("ACCOUNT_ID_UNAVAILABLE" if "account_id" in self._field_errors
                      else "COMMISSION_UNAVAILABLE")
            return HealthStatus(state=HealthState.DEGRADED, message=reason, details=details)
        age = details["age_s"]
        if age is None or age < 0 or age > self._stale_after_s:
            details["snapshot_status"]="STALE"
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_STALE, details=details)
        incomplete = any(row.get("account_id_status") != "AVAILABLE" or
                         row.get("commission_status") != "AVAILABLE" or
                         (row.get("account_id") and not row.get("broker"))
                         for row in self._positions.values())
        if incomplete:
            return HealthStatus(state=HealthState.DEGRADED,
                                message="POSITIONS_INCOMPLETE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="open=%d floating=%.2f" %
                                    (len(self._positions), details["floating_pnl"]),
                            details=details)
