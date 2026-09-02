from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.0"

EVENT_IN = "SYS_5MIN"
EVENT_OUT = "tools.device_resources.state"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_PSUTIL = "PSUTIL_UNAVAILABLE"
REASON_NO_SAMPLE = "NO_SAMPLE_YET"


def _get_psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._initialized = False
        self._running = False
        self._enable_temperature = True
        self.sample_count = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._enable_temperature = bool(context.config["enable_temperature"])
        context.subscribe(EVENT_IN, self._on_interval)
        self._initialized = True

    async def start(self) -> None:
        if not self._initialized or self._running or self._context is None:
            return
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _read_metrics(self) -> tuple[bool, dict[str, Any]]:
        psutil = _get_psutil()
        if psutil is None:
            return False, {}
        details: dict[str, Any] = {}
        unavailable: list[str] = []
        ok = False
        try:
            details["cpu_pct"] = round(float(psutil.cpu_percent(interval=None)), 1)
            ok = True
        except Exception:
            unavailable.append("cpu_pct")
        try:
            vm = psutil.virtual_memory()
            details["memory_pct"] = round(float(vm.percent), 1)
            details["memory_used_bytes"] = int(vm.used)
            details["memory_total_bytes"] = int(vm.total)
            ok = True
        except Exception:
            unavailable.append("memory")
        try:
            net = psutil.net_io_counters()
            details["net_bytes_sent"] = int(net.bytes_sent)
            details["net_bytes_recv"] = int(net.bytes_recv)
            ok = True
        except Exception:
            unavailable.append("network")
        if self._enable_temperature:
            temp = self._read_temperature(psutil)
            if temp is not None:
                details["temperature_c"] = temp
                ok = True
            else:
                unavailable.append("temperature")
        if unavailable:
            details["unavailable"] = unavailable
        return ok, details

    @staticmethod
    def _read_temperature(psutil) -> float | None:
        getter = getattr(psutil, "sensors_temperatures", None)
        if getter is None:
            return None
        try:
            groups = getter()
        except Exception:
            return None
        for readings in (groups or {}).values():
            if readings:
                return round(float(readings[0].current), 1)
        return None

    async def _on_interval(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        ok, details = self._read_metrics()
        body: dict[str, Any] = dict(details)
        body["state"] = "HEALTHY" if ok else "UNKNOWN"
        stamp = None
        if isinstance(payload, dict):
            stamp = _to_float(payload.get("official_time", payload.get("timestamp")))
        if stamp is not None:
            body["timestamp"] = stamp
        self.sample_count += 1
        await self._context.publish(EVENT_OUT, body)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        ok, details = self._read_metrics()
        details = dict(details)
        details["samples"] = self.sample_count
        if _get_psutil() is None:
            return HealthStatus(
                state=HealthState.DEGRADED, message=REASON_NO_PSUTIL, details=details)
        if not ok:
            return HealthStatus(
                state=HealthState.DEGRADED, message="ALL_METRICS_FAILED", details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="cpu=%s mem=%s" % (
                details.get("cpu_pct"), details.get("memory_pct")),
            details=details)
