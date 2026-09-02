from __future__ import annotations

import asyncio
import json
from typing import Any

import transport

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.2.0"

EVENT_PULSE = "SYS_SECOND"
EVENT_OUT = "market.reference"

PROVIDER = "yahoo"

_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0 Safari/537.36")
_ACCEPT = "application/json,text/plain,*/*"
_SILENCE_POLL_FACTOR = 5.0

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_TICK = "NO_TICK_YET"
REASON_REPEATED_FAILURE = "REPEATED_FAILURE"
REASON_SILENT = "FEED_SILENT"


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
        self._symbols: list[str] = []
        self._base_url = ""
        self._poll_interval_s = 0.0
        self._request_timeout_s = 0.0
        self._max_errors = 0
        self._polling = False
        self._poll_task: asyncio.Task | None = None
        self._now = 0.0
        self._last_poll_at = 0.0
        self._last_tick_at: float | None = None
        self._published = 0
        self._dropped_same = 0
        self._consecutive_errors = 0
        self._last_error = ""
        self._last_value: dict[str, float] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._symbols = [str(s) for s in cfg["symbols"] if str(s).strip()]
        self._base_url = str(cfg["base_url"])
        self._poll_interval_s = float(cfg["poll_interval_s"])
        self._request_timeout_s = float(cfg["request_timeout_s"])
        self._max_errors = int(cfg["max_consecutive_errors"])
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        task = self._poll_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        self._polling = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = _to_float(payload.get("official_time"))
        if now is None:
            return
        self._now = now
        if self._polling or (now - self._last_poll_at) < self._poll_interval_s:
            return
        self._last_poll_at = now
        self._polling = True
        self._poll_task = asyncio.create_task(self._poll_all())

    async def _poll_all(self) -> None:
        try:
            for symbol in self._symbols:
                if not self._running:
                    break
                await self._poll_one(symbol)
        finally:
            self._polling = False

    async def _poll_one(self, symbol: str) -> None:
        if self._context is None:
            return
        try:
            value = await asyncio.to_thread(self._fetch, symbol)
        except Exception as exc:
            self._consecutive_errors += 1
            self._last_error = "%s: %s" % (symbol, exc)
            self._context.logger.warning("620 fetch failed %s: %s", symbol, exc)
            return
        self._consecutive_errors = 0
        if value is None or value <= 0.0:
            return
        self._last_tick_at = self._now
        if self._last_value.get(symbol) == value:
            self._dropped_same += 1
            return
        self._last_value[symbol] = value
        self._published += 1
        await self._context.publish(
            EVENT_OUT, {"provider": PROVIDER, "symbol": symbol, "value": value})

    def _fetch(self, symbol: str) -> float | None:
        url = self._base_url + transport.quote(symbol)
        data = transport.http_get_json(
            url, {"User-Agent": _USER_AGENT, "Accept": _ACCEPT},
            self._request_timeout_s)
        result = (data.get("chart", {}).get("result") or [{}])[0]
        meta = result.get("meta", {})
        return _to_float(meta.get("regularMarketPrice"))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"published": self._published, "dropped_same": self._dropped_same,
                   "errors": self._consecutive_errors, "symbols": len(self._symbols)}
        if self._consecutive_errors >= self._max_errors:
            return HealthStatus(
                state=HealthState.UNHEALTHY,
                message="%s: %s" % (REASON_REPEATED_FAILURE, self._last_error),
                details=details)
        if self._last_tick_at is None:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_TICK,
                                details=details)
        silent_for = self._now - self._last_tick_at
        if silent_for > self._poll_interval_s * _SILENCE_POLL_FACTOR:
            return HealthStatus(state=HealthState.DEGRADED,
                                message="%s: %.0fs" % (REASON_SILENT, silent_for),
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="streaming %d symbols published=%d" % (
                len(self._symbols), self._published),
            details=details)
