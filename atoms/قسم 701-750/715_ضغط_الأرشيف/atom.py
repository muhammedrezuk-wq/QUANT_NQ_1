from __future__ import annotations

import asyncio
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "3.0.2"

EVENT_IN = "storage.archived"
EVENT_OUT = "storage.compressed"

REASON_NOT_STARTED = "NOT_STARTED"

_SUFFIX = ".gz"
_CHUNK_BYTES = 1024 * 1024


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._initialized = False
        self._running = False
        self._min_size_bytes = 0
        self._level = 0
        self._keep_original = True
        self._active_archive_path = ""
        self._processed: set[str] = set()
        self._runs = 0
        self._compressed_count = 0
        self._saved_bytes = 0
        self._last_error = ""
        self._last_result: dict[str, Any] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._min_size_bytes = int(cfg["min_size_bytes"])
        self._level = int(cfg["compression_level"])
        self._keep_original = bool(cfg["keep_original"])
        self._active_archive_path = str(cfg.get("active_archive_db_path") or "")
        context.subscribe(EVENT_IN, self._on_archived)
        self._initialized = True

    async def start(self) -> None:
        if not self._initialized or self._running or self._context is None:
            return
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    @staticmethod
    def _digest(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    @staticmethod
    def _verify_gzip(path: Path, expected_size: int, expected_digest: str) -> bool:
        digest = hashlib.sha256()
        size = 0
        try:
            with gzip.open(path, "rb") as stream:
                while True:
                    chunk = stream.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
        except (OSError, EOFError):
            return False
        return size == expected_size and digest.hexdigest() == expected_digest

    def _active_reason(self, path: Path, declared_active: Any = None) -> str | None:
        source_id = _identity(path)
        active_values = [self._active_archive_path, declared_active]
        if any(value and source_id == _identity(Path(str(value))) for value in active_values):
            return "ACTIVE_ARCHIVE_PATH"
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm")):
            return "ACTIVE_ARCHIVE_SIDECAR"
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(
                    path.as_uri() + "?mode=rw", uri=True, timeout=0.0)
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    return "ACTIVE_ARCHIVE_LOCKED"
            except sqlite3.DatabaseError:
                pass
            finally:
                if connection is not None:
                    connection.close()
        return None

    def _failure(self, reason: str, **extra: Any) -> dict[str, Any]:
        self._last_error = reason
        return {"compressed": False, "reason": reason, **extra}

    def _compress(self, source: str, declared_active: Any = None) -> dict[str, Any]:
        path = Path(source)
        source_id = _identity(path)
        if source_id in self._processed:
            self._last_error = ""
            return {"compressed": False, "reason": "ALREADY_PROCESSED"}
        if path.suffix.lower() == _SUFFIX:
            return self._failure("ALREADY_COMPRESSED")
        if not path.is_file():
            return self._failure("MISSING")
        active_reason = self._active_reason(path, declared_active)
        if active_reason:
            return self._failure(active_reason)
        size, digest = self._digest(path)
        if size < self._min_size_bytes:
            self._last_error = ""
            return {"compressed": False, "reason": "BELOW_MIN_SIZE",
                    "size_bytes": size}
        target = path.with_suffix(path.suffix + _SUFFIX)
        if target.exists():
            return self._failure("TARGET_EXISTS")
        fd, temp_name = tempfile.mkstemp(
            dir=path.parent, prefix="." + target.name + ".", suffix=".tmp")
        os.close(fd)
        temp = Path(temp_name)
        try:
            with path.open("rb") as raw, temp.open("wb") as raw_packed:
                with gzip.GzipFile(fileobj=raw_packed, mode="wb",
                                   compresslevel=self._level) as packed:
                    shutil.copyfileobj(raw, packed, length=_CHUNK_BYTES)
                raw_packed.flush()
                os.fsync(raw_packed.fileno())
            if not self._verify_gzip(temp, size, digest):
                raise OSError("VERIFY_FAILED")
            current_size, current_digest = self._digest(path)
            if current_size != size or current_digest != digest:
                raise OSError("SOURCE_CHANGED_DURING_COMPRESSION")
            packed_size = temp.stat().st_size
            os.replace(temp, target)
            original_kept = True
            if not self._keep_original:
                try:
                    path.unlink()
                    original_kept = False
                except OSError as exc:
                    self._last_error = "ORIGINAL_DELETE_FAILED: " + str(exc)
                    return {"compressed": True, "path": str(target),
                            "size_bytes": size, "packed_bytes": packed_size,
                            "saved_bytes": max(0, size - packed_size),
                            "original_kept": True,
                            "reason": "ORIGINAL_DELETE_FAILED"}
            self._processed.add(source_id)
            self._last_error = ""
            return {"compressed": True, "path": str(target),
                    "size_bytes": size, "packed_bytes": packed_size,
                    "saved_bytes": max(0, size - packed_size),
                    "original_kept": original_kept,
                    "sha256": digest, "verified": True}
        except (OSError, EOFError) as exc:
            temp.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            return self._failure(str(exc) or "WRITE_FAILED")
        finally:
            temp.unlink(missing_ok=True)

    async def _on_archived(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        rows = _to_int(payload.get("rows"))
        source = payload.get("archive_path")
        if not source or not rows:
            return
        result = await asyncio.to_thread(
            self._compress, str(source), payload.get("active_archive_path"))
        self._runs += 1
        self._last_result = result
        if result.get("compressed"):
            self._compressed_count += 1
            self._saved_bytes += int(result.get("saved_bytes") or 0)
        body: dict[str, Any] = dict(result)
        body["total_compressed"] = self._compressed_count
        body["total_saved_bytes"] = self._saved_bytes
        stamp = payload.get("timestamp")
        if isinstance(stamp, (int, float)):
            body["timestamp"] = stamp
        await self._context.publish(EVENT_OUT, body)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "processed": sorted(self._processed),
                "runs": self._runs, "compressed": self._compressed_count,
                "saved_bytes": self._saved_bytes}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or not isinstance(state.get("processed", []), list):
            raise ValueError("INVALID_ARCHIVE_COMPRESSION_STATE")
        self._processed = {str(value) for value in state.get("processed", []) if str(value)}
        self._runs = int(state.get("runs") or 0)
        self._compressed_count = int(state.get("compressed") or 0)
        self._saved_bytes = int(state.get("saved_bytes") or 0)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"runs": self._runs, "compressed": self._compressed_count,
                   "saved_bytes": self._saved_bytes,
                   "keep_original": self._keep_original,
                   "active_archive_path": self._active_archive_path,
                   "processed": len(self._processed),
                   "last_result": dict(self._last_result),
                   "last_error": self._last_error}
        if self._last_error:
            return HealthStatus(
                state=HealthState.DEGRADED, message=self._last_error, details=details)
        if self._runs == 0:
            return HealthStatus(
                state=HealthState.HEALTHY,
                message="READY_AWAITING_FIRST_ROTATED_ARCHIVE_714 | runs=0",
                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="compressed=%d saved=%d" % (
                self._compressed_count, self._saved_bytes), details=details)
