from __future__ import annotations

import math
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "5.3.0"
EVENT_IN = "market.tick.validated"
EVENT_OUT = "market_data.candle_closed"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_CANDLES = "NO_CANDLES_YET"
# Campaign 1-449, batch A (owner order 2026-08-23): the candle builder becomes
# a FRAME FACTORY per the owner's vision -- the SAME ticks build every
# configured timeframe simultaneously ("tick, 5 seconds, a minute, 15 minutes,
# an hour, 4 hours, a day ... each release is a batch -- every consumer takes
# the candle that suits it"). A frame is data, never code: enable or disable
# a timeframe by config alone. The default keeps the historical single 60s
# frame -- zero behavior change until the owner dials more frames in.
DEFAULT_TIMEFRAMES = ("60s",)

_TIMEFRAME_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timeframe_seconds(text_value: Any) -> float | None:
    text = str(text_value or "").strip().lower()
    if len(text) < 2 or text[-1] not in _TIMEFRAME_UNITS:
        return None
    try:
        amount = float(text[:-1])
    except ValueError:
        return None
    seconds = amount * _TIMEFRAME_UNITS[text[-1]]
    return seconds if seconds > 0 else None


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._frames: tuple[tuple[str, float], ...] = ()
        # One candle state per (account, broker, symbol, timeframe-seconds).
        self._candles: dict[tuple[str, str, str, float], dict[str, Any]] = {}
        self._closed_periods: dict[tuple[str, str, str, float], set[float]] = {}
        self._last_seq: dict[tuple[str, str, str], int] = {}
        self._last_tick: dict[tuple[str, str, str], tuple] = {}
        self.candles_closed_count = 0
        self.closed_by_frame: dict[str, int] = {}
        self.batch_releases = 0
        self.out_of_order_dropped = 0
        self.duplicates_dropped = 0
        self.late_dropped = 0
        self.gap_affected_candles = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        raw = context.config.get("timeframes") or list(DEFAULT_TIMEFRAMES)
        frames: list[tuple[str, float]] = []
        seen: set[float] = set()
        for item in raw if isinstance(raw, list) else [raw]:
            seconds = _timeframe_seconds(item)
            if seconds is None or seconds in seen:
                continue
            seen.add(seconds)
            frames.append((str(item).strip().lower(), seconds))
        self._frames = tuple(frames) or (
            (DEFAULT_TIMEFRAMES[0], _timeframe_seconds(DEFAULT_TIMEFRAMES[0]) or 60.0),)
        context.subscribe(EVENT_IN, self._on_tick)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").strip()
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        bid = _to_float(payload.get("bid"))
        ask = _to_float(payload.get("ask"))
        timestamp = _to_float(payload.get("timestamp"))
        if (not symbol or not account or not broker or bid is None or ask is None
                or timestamp is None or bid <= 0 or ask < bid or timestamp <= 0):
            return
        key = (account, broker, symbol)
        mid = (bid + ask) / 2.0
        volume = _to_float(payload.get("volume"))
        fabric = bool(payload.get("fabric_gap"))

        seq = payload.get("sequence")
        if seq is not None:
            try:
                seq_i = int(seq)
            except (TypeError, ValueError):
                seq_i = None
            if seq_i is not None:
                last = self._last_seq.get(key)
                if last is not None and seq_i <= last:
                    self.out_of_order_dropped += 1
                    return
                self._last_seq[key] = seq_i

        tick_id = (timestamp, mid)
        if self._last_tick.get(key) == tick_id:
            self.duplicates_dropped += 1
            return
        self._last_tick[key] = tick_id

        released_this_tick = 0
        for frame_name, frame_seconds in self._frames:
            frame_key = (account, broker, symbol, frame_seconds)
            period_start = timestamp - (timestamp % frame_seconds)
            if period_start in self._closed_periods.get(frame_key, ()):
                self.late_dropped += 1
                continue
            current = self._candles.get(frame_key)
            if current is not None and current["period_start"] != period_start:
                await self._close_candle(frame_key, frame_name, frame_seconds, current)
                self._closed_periods.setdefault(frame_key, set()).add(current["period_start"])
                released_this_tick += 1
                current = None
            if current is None:
                self._candles[frame_key] = {
                    "period_start": period_start, "open": mid, "high": mid,
                    "low": mid, "close": mid, "tick_count": 1,
                    "volume": max(0.0, volume or 0.0), "provider": payload.get("provider"),
                    "fabric_gap": fabric}
                if fabric:
                    self.gap_affected_candles += 1
            else:
                if fabric and not current.get("fabric_gap"):
                    current["fabric_gap"] = True
                    self.gap_affected_candles += 1
                current["high"] = max(current["high"], mid)
                current["low"] = min(current["low"], mid)
                current["close"] = mid
                current["tick_count"] += 1
                current["volume"] += max(0.0, volume or 0.0)
        if released_this_tick > 1:
            self.batch_releases += 1

    async def _close_candle(self, frame_key: tuple[str, str, str, float],
                            frame_name: str, frame_seconds: float,
                            candle: dict[str, Any]) -> None:
        if self._context is None:
            return
        account, broker, symbol, _ = frame_key
        self.candles_closed_count += 1
        self.closed_by_frame[frame_name] = self.closed_by_frame.get(frame_name, 0) + 1
        await self._context.publish(EVENT_OUT, {
            "account_id": account, "broker": broker, "provider": candle.get("provider"),
            "symbol": symbol, "open": candle["open"], "high": candle["high"],
            "low": candle["low"], "close": candle["close"],
            "volume": candle["volume"], "tick_count": candle["tick_count"],
            "fabric_gap": bool(candle.get("fabric_gap", False)),
            "timeframe": frame_name,
            "period_start": candle["period_start"], "timestamp": candle["period_start"]})

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"closed": self.candles_closed_count,
                   "frames": [name for name, _ in self._frames],
                   "closed_by_frame": dict(self.closed_by_frame),
                   "batch_releases": self.batch_releases,
                   "out_of_order_dropped": self.out_of_order_dropped,
                   "duplicates_dropped": self.duplicates_dropped,
                   "late_dropped": self.late_dropped,
                   "gap_affected_candles": self.gap_affected_candles,
                   "active_scopes": [list(k) for k in self._candles]}
        if self.candles_closed_count == 0:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_NO_CANDLES, details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="closed=%d frames=%d batch=%d ooo=%d dup=%d late=%d gap=%d" % (
                self.candles_closed_count, len(self._frames), self.batch_releases,
                self.out_of_order_dropped, self.duplicates_dropped,
                self.late_dropped, self.gap_affected_candles),
            details=details)
