from __future__ import annotations

import math
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "3.0.1"
EVENT_IN_DRIFT = "time.utc.drift"
EVENT_IN_SYNCED = "time.utc.synced"
EVENT_HEARTBEAT = "kernel.clock.heartbeat"
EVENT_OUT_DRIFT = "market.time.drift"
EVENT_OUT_SYNCED = "market.time.synced"
EVENT_DIVERGENCE = "time.clock.divergence"
REASON_NOT_STARTED = "NOT_STARTED"


def _finite(value: Any) -> float | None:
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if math.isfinite(result) else None


def _sequence(payload: dict[str, Any]) -> int | None:
    value = payload.get("sequence", payload.get("clock_sequence"))
    if isinstance(value, bool): return None
    try: sequence = int(value)
    except (TypeError, ValueError): return None
    return sequence if sequence >= 1 and value == sequence else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None; self._running = False
        self._max_age_s = 5.0; self._divergence_threshold_s = 0.5
        self.drift_count = self.synced_count = self.rejected_count = 0
        self.divergence_count = 0; self._last_heartbeat_mono: float | None = None
        self._last_sync_mono: float | None = None
        self._last_divergence_s: float | None = None; self._last_error = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context; self._max_age_s = float(context.config["max_age_s"])
        self._divergence_threshold_s = float(context.config["divergence_threshold_s"])
        context.subscribe(EVENT_IN_DRIFT, self._on_drift)
        context.subscribe(EVENT_IN_SYNCED, self._on_synced)
        context.subscribe(EVENT_HEARTBEAT, self._on_heartbeat)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _valid_common(self, payload: Any) -> bool:
        if not isinstance(payload, dict): return False
        quality = str(payload.get("clock_quality") or "")
        sync_age = _finite(payload.get("sync_age_s"))
        return (_finite(payload.get("offset_s")) is not None
                and _finite(payload.get("effective_offset_s")) is not None
                and _finite(payload.get("target_offset_s")) is not None
                and _finite(payload.get("measured_at")) is not None
                and sync_age is not None and sync_age >= 0
                and _sequence(payload) is not None
                and quality in (clock.SYNCED, clock.STALE,
                                clock.LOCAL_FALLBACK, clock.INVALID))

    async def _on_drift(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None: return
        if not self._valid_common(payload) or _finite(payload.get("threshold_s")) is None:
            self.rejected_count += 1
            self._last_error = "MALFORMED_DRIFT"
            return
        self.drift_count += 1
        await self._context.publish(EVENT_OUT_DRIFT, {key: payload.get(key) for key in
            ("offset_s", "effective_offset_s", "target_offset_s", "measured_at",
             "threshold_s", "clock_quality", "clock_sequence", "sync_age_s")})

    async def _on_synced(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None: return
        if not self._valid_common(payload):
            self.rejected_count += 1
            self._last_error = "MALFORMED_SYNC"
            return
        quality = str(payload.get("clock_quality") or "")
        if quality not in (clock.SYNCED, clock.STALE, clock.LOCAL_FALLBACK, clock.INVALID):
            self.rejected_count += 1
            self._last_error = "INVALID_CLOCK_QUALITY"
            return
        self.synced_count += 1; self._last_sync_mono = clock.mono(); self._last_error = ""
        await self._context.publish(EVENT_OUT_SYNCED, {key: payload.get(key) for key in
            ("offset_s", "effective_offset_s", "target_offset_s", "measured_at",
             "sample_id", "clock_quality", "clock_sequence", "sync_age_s")})

    async def _on_heartbeat(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        official = _finite(payload.get("official_time")); bus_stamp = _finite(payload.get("timestamp"))
        quality = str(payload.get("clock_quality") or "")
        if (official is None or bus_stamp is None
                or quality not in (clock.SYNCED, clock.STALE,
                                   clock.LOCAL_FALLBACK, clock.INVALID)):
            self.rejected_count += 1; self._last_error = "MALFORMED_HEARTBEAT"; return
        self._last_heartbeat_mono = clock.mono(); self._last_error = ""
        divergence = abs(official - bus_stamp)
        self._last_divergence_s = (divergence
                                   if divergence > self._divergence_threshold_s else None)
        if self._last_divergence_s is not None:
            self.divergence_count += 1
            await self._context.publish(EVENT_DIVERGENCE, {
                "status": "DEGRADED", "clock_time": official,
                "event_bus_time": bus_stamp, "divergence_s": divergence,
                "threshold_s": self._divergence_threshold_s,
                "clock_quality": payload.get("clock_quality")})

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        mono_now = clock.mono()
        heartbeat_age = None if self._last_heartbeat_mono is None else mono_now - self._last_heartbeat_mono
        sync_age = None if self._last_sync_mono is None else mono_now - self._last_sync_mono
        details = {"drift": self.drift_count, "synced": self.synced_count,
            "rejected": self.rejected_count, "divergence": self.divergence_count,
            "last_divergence_s": self._last_divergence_s,
            "heartbeat_age_s": heartbeat_age, "bridge_sync_age_s": sync_age,
            "clock": clock.state(), "last_error": self._last_error}
        if self._last_error: return HealthStatus(state=HealthState.DEGRADED, message=self._last_error, details=details)
        if heartbeat_age is None or heartbeat_age > self._max_age_s:
            return HealthStatus(state=HealthState.DEGRADED, message="CLOCK_HEARTBEAT_STALE", details=details)
        if clock.quality() != clock.SYNCED:
            return HealthStatus(state=HealthState.DEGRADED, message=f"CLOCK_{clock.quality()}", details=details)
        if self._last_divergence_s is not None:
            return HealthStatus(state=HealthState.DEGRADED, message="CLOCK_DIVERGENCE",
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
            message=f"synced={self.synced_count} divergence={self.divergence_count}", details=details)
