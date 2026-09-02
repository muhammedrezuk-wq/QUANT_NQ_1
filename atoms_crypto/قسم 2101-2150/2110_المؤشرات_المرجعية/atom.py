from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.0"

EVENT_OUT = "market_data.reference_index_updated"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_SOURCE = "UNAVAILABLE_NO_REFERENCE_SOURCE"

BAD_SHAPE = "shape"
BAD_VALUE = "bad_value"
NOT_WATCHED = "not_watched"


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
        self._source_event = ""
        self._watched: list[str] = []
        self._min_change_pct = 0.0
        self._last: dict[str, float] = {}
        self._session_open: dict[str, float] = {}
        self._received = 0
        self._published = 0
        self._rejected: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._source_event = str(cfg["source_event"])
        self._watched = [str(s).upper() for s in cfg["watched_symbols"]]
        self._min_change_pct = float(cfg["min_change_pct"])
        context.subscribe(self._source_event, self._on_index)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _reject(self, reason: str) -> None:
        self._rejected[reason] = self._rejected.get(reason, 0) + 1

    async def _on_index(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        self._received += 1
        symbol = payload.get("symbol")
        value = _to_float(payload.get("value", payload.get("price")))
        if not isinstance(symbol, str) or not symbol.strip():
            self._reject(BAD_SHAPE)
            return
        symbol = symbol.strip().upper()
        if value is None or value <= 0:
            self._reject(BAD_VALUE)
            return
        if self._watched and symbol not in self._watched:
            self._reject(NOT_WATCHED)
            return
        previous = self._last.get(symbol)
        self._last[symbol] = value
        self._session_open.setdefault(symbol, value)
        change_pct = None
        if previous is not None and previous > 0:
            change_pct = round((value - previous) / previous * 100.0, 6)
            if abs(change_pct) < self._min_change_pct:
                return
        open_value = self._session_open[symbol]
        out: dict[str, Any] = {
            "symbol": symbol, "value": value, "previous": previous,
            "change_pct": change_pct, "session_open": open_value,
            "change_from_open_pct": round((value - open_value) / open_value * 100.0, 6)
            if open_value > 0 else None,
        }
        provider = payload.get("provider")
        if provider is not None:
            out["provider"] = provider
        ts = payload.get("timestamp")
        if isinstance(ts, (int, float)):
            out["timestamp"] = ts
        self._published += 1
        await self._context.publish(EVENT_OUT, out)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"received": self._received, "published": self._published,
                   "tracked": len(self._last), "watched": len(self._watched),
                   "rejected": {k: v for k, v in self._rejected.items() if v}}
        if self._received == 0:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NO_SOURCE, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="tracked=%d published=%d" % (len(self._last), self._published),
            details=details)
