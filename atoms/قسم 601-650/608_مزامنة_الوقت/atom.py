from __future__ import annotations

import asyncio
import math
import statistics
import struct
import time
from typing import Any

import transport
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.3.0"
SUBSECOND_CLOCK_REASON = "NTP round-trip is measured in milliseconds"
EVENT_SAMPLE = "time.ntp.samples.state"
EVENT_DRIFT = "time.ntp.drift"
EVENT_STALE = "time.utc.stale"
_FAILURE_BACKOFF_S = (5.0, 15.0, 60.0)
_NTP_EPOCH_OFFSET = 2_208_988_800
_NTP_PACKET_BYTES = 48
_NTP_PORT = 123
_NTP_REQUEST = b"\x1b" + 47 * b"\0"
_NTP_TX_START = 40
_NTP_TX_END = 48
_NTP_ORIGIN_START = 24
_NTP_ORIGIN_END = 32
_NTP_FRACTION_DIVISOR = 2 ** 32
_NTP_MODE_MASK = 7
_NTP_VERSION_MASK = 7
_NTP_VERSION_SHIFT = 3
_NTP_LEAP_SHIFT = 6
_NTP_SERVER_MODES = (4, 5)
_NTP_SUPPORTED_VERSIONS = (3, 4)
_NTP_UNSYNCHRONIZED = 3
_NTP_MIN_STRATUM = 1
_NTP_MAX_STRATUM = 15
_MIN_ACCEPTED_OFFSET_S = 0.01
_HALF_ROUND_TRIP = 2.0
_ROUND_DP = 6
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NEVER_SYNCED = "NEVER_SAMPLED"
REASON_UNREACHABLE = "REFERENCE_UNREACHABLE"
REASON_STALE = "LAST_SAMPLE_STALE"

class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None; self._running = False
        self._task: asyncio.Task | None = None; self._servers: list[str] = []
        self._sync_interval_s = self._query_timeout_s = self._drift_alert_s = 0.0
        self._stale_after_s = 0.0; self._max_accepted_offset_s = 5.0
        self._max_sample_deviation_s = 0.25; self._min_samples = 2
        self._last_offset_s: float | None = None; self._last_sync_at: float | None = None
        self._last_server = ""; self._last_error = ""; self.sync_count = 0
        self.drift_count = 0; self.failure_count = 0; self.rejected_samples = 0
        self._consecutive_failures = 0; self._stale_announced_for_sync = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context; cfg = context.config
        self._servers = [str(s).strip() for s in cfg["reference_servers"] if str(s).strip()]
        self._sync_interval_s = float(cfg["sync_interval_s"])
        self._query_timeout_s = float(cfg["query_timeout_s"])
        self._drift_alert_s = float(cfg["drift_alert_s"])
        self._stale_after_s = float(cfg["stale_after_s"])
        self._max_accepted_offset_s = max(_MIN_ACCEPTED_OFFSET_S, float(cfg["max_accepted_offset_s"]))
        self._max_sample_deviation_s = float(cfg["max_sample_deviation_s"])
        self._min_samples = int(cfg["min_samples"])

    async def start(self) -> None:
        if self._running or self._context is None: return
        self._running = True; self._task = asyncio.create_task(self._sync_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
        self._task = None

    async def shutdown(self) -> None: await self.stop()

    def _query(self, host: str) -> dict[str, Any]:
        sent_at = time.time(); request = bytearray(_NTP_REQUEST)
        ntp_sent = sent_at + _NTP_EPOCH_OFFSET; seconds = int(ntp_sent)
        fraction = int((ntp_sent - seconds) * _NTP_FRACTION_DIVISOR)
        request[_NTP_TX_START:_NTP_TX_END] = struct.pack("!II", seconds, fraction)
        data = transport.udp_exchange(host, _NTP_PORT, bytes(request),
                                      _NTP_PACKET_BYTES, self._query_timeout_s)
        received_at = time.time()
        if len(data) != _NTP_PACKET_BYTES: raise ValueError("NTP reply length invalid")
        mode = data[0] & _NTP_MODE_MASK
        version = (data[0] >> _NTP_VERSION_SHIFT) & _NTP_VERSION_MASK
        leap = data[0] >> _NTP_LEAP_SHIFT; stratum = data[1]
        if (mode not in _NTP_SERVER_MODES or version not in _NTP_SUPPORTED_VERSIONS
                or leap == _NTP_UNSYNCHRONIZED
                or not _NTP_MIN_STRATUM <= stratum <= _NTP_MAX_STRATUM):
            raise ValueError("NTP reply mode/version/leap/stratum invalid")
        if data[_NTP_ORIGIN_START:_NTP_ORIGIN_END] != bytes(request[_NTP_TX_START:_NTP_TX_END]):
            raise ValueError("NTP originate timestamp mismatch")
        seconds, fraction = struct.unpack("!II", data[_NTP_TX_START:_NTP_TX_END])
        server_time = seconds + fraction / _NTP_FRACTION_DIVISOR - _NTP_EPOCH_OFFSET
        round_trip = received_at - sent_at
        if not math.isfinite(round_trip) or round_trip < 0 or round_trip > self._query_timeout_s:
            raise ValueError("NTP round-trip invalid")
        offset = server_time - (received_at - round_trip / _HALF_ROUND_TRIP)
        if not math.isfinite(offset) or abs(offset) > self._max_accepted_offset_s:
            raise ValueError("NTP offset outside accepted bound")
        return {"server": host, "offset_s": offset, "round_trip_s": round_trip,
                "stratum": stratum}

    async def _query_safe(self, host: str) -> tuple[dict[str, Any] | None, str | None]:
        try: return await asyncio.to_thread(self._query, host), None
        except Exception as exc:
            if self._context is not None: self._context.logger.warning("608 reference %s failed: %s", host, exc)
            return None, f"{host}: {exc}"

    async def _sync_once(self) -> bool:
        if self._context is None: return False
        queried = await asyncio.gather(*(self._query_safe(host) for host in self._servers))
        samples = [sample for sample, _ in queried if sample is not None]
        errors = [error for _, error in queried if error]
        if not samples:
            self.failure_count += 1; self._consecutive_failures += 1
            self._last_error = "; ".join(errors); return False
        center = statistics.median(float(sample["offset_s"]) for sample in samples)
        accepted = [sample for sample in samples
                    if abs(float(sample["offset_s"]) - center) <= self._max_sample_deviation_s]
        rejected = len(samples) - len(accepted); self.rejected_samples += rejected
        if len(accepted) < self._min_samples:
            self.failure_count += 1; self._consecutive_failures += 1
            self._last_error = "NTP sample quorum not reached"; return False
        median_offset = statistics.median(float(sample["offset_s"]) for sample in accepted)
        measured_at = time.time(); previous = self._last_offset_s
        self._last_offset_s = median_offset; self._last_sync_at = measured_at
        self._last_server = ",".join(sorted(str(sample["server"]) for sample in accepted))
        self._last_error = ""; self.sync_count += 1
        self._consecutive_failures = 0; self._stale_announced_for_sync = 0
        body = {"sample_id": f"{measured_at:.9f}:{self.sync_count}",
                "measured_at": measured_at,
                "median_offset_s": round(median_offset, _ROUND_DP),
                "quorum": True, "accepted_count": len(accepted),
                "required_count": self._min_samples, "queried_count": len(self._servers),
                "rejected_count": rejected + len(errors),
                "samples": [{"server": sample["server"],
                    "offset_s": round(float(sample["offset_s"]), _ROUND_DP),
                    "round_trip_s": round(float(sample["round_trip_s"]), _ROUND_DP),
                    "stratum": sample.get("stratum")} for sample in accepted],
                "errors": errors}
        await self._context.publish(EVENT_SAMPLE, body)
        if abs(median_offset) >= self._drift_alert_s:
            self.drift_count += 1
            await self._context.publish(EVENT_DRIFT, {**body, "threshold_s": self._drift_alert_s,
                "previous_offset_s": previous, "drift_count": self.drift_count})
        return True

    def _next_delay(self, succeeded: bool) -> float:
        if succeeded or self._consecutive_failures > len(_FAILURE_BACKOFF_S):
            return self._sync_interval_s
        return _FAILURE_BACKOFF_S[self._consecutive_failures - 1]

    async def _publish_stale_if_due(self) -> None:
        if (self._context is None or self._last_sync_at is None
                or self._stale_announced_for_sync == self.sync_count):
            return
        age = time.time() - self._last_sync_at
        if not math.isfinite(age) or age <= self._stale_after_s:
            return
        self._stale_announced_for_sync = self.sync_count
        await self._context.publish(EVENT_STALE, {
            "measured_at": self._last_sync_at,
            "sync_age_s": age, "offset_s": self._last_offset_s,
            "clock_quality": "STALE", "sync_count": self.sync_count,
            "failure_count": self.failure_count,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error})

    async def _sync_loop(self) -> None:
        try:
            while self._running:
                succeeded = await self._sync_once()
                try:
                    await self._publish_stale_if_due()
                except Exception as exc:
                    if self._context is not None:
                        self._context.logger.error("608 stale event failed: %s", exc)
                await asyncio.sleep(self._next_delay(succeeded))
        except asyncio.CancelledError:
            pass

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"sync_count": self.sync_count, "drift_count": self.drift_count,
            "failure_count": self.failure_count,
            "consecutive_failures": self._consecutive_failures,
            "rejected_samples": self.rejected_samples,
            "last_offset_s": self._last_offset_s, "last_servers": self._last_server,
            "last_error": self._last_error}
        if self._last_sync_at is None:
            return HealthStatus(state=HealthState.DEGRADED,
                message=REASON_NEVER_SYNCED if not self._last_error else REASON_UNREACHABLE,
                details=details)
        if time.time() - self._last_sync_at > self._stale_after_s:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_STALE, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
            message=f"median_offset={self._last_offset_s or 0.0:.6f}s via {self._last_server}", details=details)
