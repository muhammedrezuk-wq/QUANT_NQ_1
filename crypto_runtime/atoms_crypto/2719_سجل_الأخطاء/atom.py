from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.2"

TARGET_CORE_LOGGER = "asmar.core"
_MARKER = "_nq_719_error_log_handler"

_LEVEL_AR = {"INFO": "INFO", "WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL"}

REASON_NOT_STARTED = "NOT_STARTED"
REASON_WRITE_FAILED = "LOG_WRITE_FAILED"
REASON_DETACHED = "HANDLER_DETACHED"


class _Collector(logging.Handler):
    def __init__(self, sink, level: int) -> None:
        super().__init__(level=level)
        setattr(self, _MARKER, True)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self._sink(record)
        except Exception:  # noqa: BLE001
            pass


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._dir = Path("var/logs")
        self._prefix = "errors"
        self._min_level = logging.WARNING
        self._include_root = True
        self._max_lines_per_day = 20000
        self._write_lock = threading.Lock()
        self._reentry = threading.local()
        self._day = ""
        self._written_today = 0
        self._suppressed_today = 0
        self._cap_announced = False
        self._total_written = 0
        self._io_failures = 0
        self._last_io_error = ""
        self._last_line = ""

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._dir = Path(str(cfg["dir"]))
        self._prefix = str(cfg["file_prefix"])
        self._min_level = int(getattr(logging, str(cfg["min_level"])))
        self._include_root = bool(cfg["include_root"])
        self._max_lines_per_day = int(cfg["max_lines_per_day"])
        self._dir.mkdir(parents=True, exist_ok=True)

    def _targets(self) -> list[tuple[str, logging.Logger]]:
        targets = [(TARGET_CORE_LOGGER, logging.getLogger(TARGET_CORE_LOGGER))]
        if self._include_root:
            targets.append(("root", logging.getLogger()))
        return targets

    @staticmethod
    def _purge(logger: logging.Logger) -> None:
        for handler in list(logger.handlers):
            if getattr(handler, _MARKER, False):
                logger.removeHandler(handler)

    async def start(self) -> None:
        if self._running or self._context is None:
            return
        for _, logger in self._targets():
            self._purge(logger)
            logger.addHandler(_Collector(self._on_record, self._min_level))
        self._running = True

    async def stop(self) -> None:
        self._running = False
        for name in (TARGET_CORE_LOGGER, ""):
            self._purge(logging.getLogger(name) if name else logging.getLogger())

    async def shutdown(self) -> None:
        await self.stop()

    def _today_path(self, day: str) -> Path:
        return self._dir / f"{self._prefix}-{day}.log"

    def _format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        level_ar = _LEVEL_AR.get(record.levelname, record.levelname)
        atom_id = getattr(record, "atom_id", None)
        if isinstance(atom_id, int):
            source = "ATOM %d" % atom_id
        elif record.name.startswith("asmar"):
            source = "CORE"
        else:
            source = "EXTERNAL"
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = repr(getattr(record, "msg", "?"))
        line = f"{stamp} | {level_ar} | {source} | {record.name} | {message}"
        if record.exc_info and record.exc_info[0] is not None:
            try:
                detail = "".join(traceback.format_exception(*record.exc_info))
                line += "\n" + "\n".join(
                    "    " + part for part in detail.rstrip().splitlines())
            except Exception:  # noqa: BLE001
                pass
        return line

    def _append(self, day: str, text: str) -> None:
        path = self._today_path(day)
        fresh = not path.exists()
        with open(path, "a", encoding="utf-8") as fh:
            if fresh:
                fh.write("- ASMAR ERROR LOG - day %s-%s-%s - all times machine-local -\n"
                         % (day[0:4], day[4:6], day[6:8]))
            fh.write(text + "\n")

    def _on_record(self, record: logging.LogRecord) -> None:
        if not self._running:
            return
        if getattr(self._reentry, "busy", False):
            return
        self._reentry.busy = True
        try:
            day = time.strftime("%Y%m%d")
            with self._write_lock:
                if day != self._day:
                    self._day = day
                    self._written_today = 0
                    self._suppressed_today = 0
                    self._cap_announced = False
                if self._written_today >= self._max_lines_per_day:
                    self._suppressed_today += 1
                    if not self._cap_announced:
                        self._cap_announced = True
                        try:
                            self._append(day, "⛔ ERROR_LOG_DAILY_CAP_REACHED (%d lines) - "
                                              "remaining errors today are counted, not written "
                                              "(suppressed in atom 719 health details)"
                                         % self._max_lines_per_day)
                        except OSError:
                            pass
                    return
                line = self._format(record)
                try:
                    self._append(day, line)
                except OSError as exc:
                    self._io_failures += 1
                    self._last_io_error = str(exc)
                    return
                self._written_today += 1
                self._total_written += 1
                self._last_io_error = ""
                self._last_line = line.splitlines()[0][:300]
        finally:
            self._reentry.busy = False

    def _attached(self) -> list[str]:
        return [name for name, logger in self._targets()
                if any(getattr(h, _MARKER, False) for h in logger.handlers)]

    def _reattach_missing(self) -> None:
        for name, logger in self._targets():
            if not any(getattr(h, _MARKER, False) for h in logger.handlers):
                logger.addHandler(_Collector(self._on_record, self._min_level))
                self._reattachments = getattr(self, "_reattachments", 0) + 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        self._reattach_missing()
        attached = self._attached()
        day = time.strftime("%Y%m%d")
        details: dict[str, Any] = {
            "file": str(self._today_path(day)),
            "written_today": self._written_today if day == self._day else 0,
            "suppressed_today": self._suppressed_today if day == self._day else 0,
            "total_written": self._total_written,
            "io_failures": self._io_failures,
            "last_io_error": self._last_io_error,
            "last_line": self._last_line,
            "attached": attached,
            "reattachments": getattr(self, "_reattachments", 0),
            "min_level": logging.getLevelName(self._min_level),
        }
        if len(attached) < len(self._targets()):
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_DETACHED, details=details)
        if self._last_io_error:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_WRITE_FAILED, details=details)
        if self._total_written == 0:
            return HealthStatus(
                state=HealthState.HEALTHY,
                message="READY_ATTACHED_AWAITING_FIRST_ERROR | written=0",
                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="today=%d total=%d" % (details["written_today"], self._total_written),
            details=details)
