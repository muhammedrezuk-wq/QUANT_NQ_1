from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "4.2.1"

EVENT_PULSE = "SYS_HOUR"
EVENT_VIOLATION = "tools.integrity.violation"

_CHUNK_BYTES = 65536
_MAX_SUMMARY_ITEMS = 6
_MISSING = "\x00missing"
_ERROR_PREFIX = "\x00error:"
_DEFAULT_IGNORED_DIRS = ("__pycache__", ".git", ".pytest_cache", ".mypy_cache")
_DEFAULT_IGNORED_SUFFIXES = (".pyc", ".pyo", ".pyd", ".tmp", ".log")


def _sha256_of_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._watched_files: list[str] = []
        self._watched_dirs: list[str] = []
        self._watched_extensions: set[str] = set()
        self._watched_names: set[str] = set()
        self._min_watched_items = 1
        self._ignored_dirs: set[str] = set(_DEFAULT_IGNORED_DIRS)
        self._ignored_suffixes: tuple[str, ...] = _DEFAULT_IGNORED_SUFFIXES
        self._baseline: dict[str, str] = {}
        self._baseline_file = ""
        self._baseline_error = ""
        self._established = False
        self._scope_digest = ""
        self._last_violations: list[dict[str, Any]] = []
        self._watched_count = 0
        self._scan_ms = 0.0
        self._last_health = HealthStatus(
            state=HealthState.UNHEALTHY, message="UNTRUSTED")

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._watched_files = [str(value) for value in cfg.get("watched_files", [])]
        self._watched_dirs = [str(value) for value in cfg.get("watched_dirs", [])]
        self._watched_extensions = {str(value).lower()
                                    for value in cfg.get("watched_extensions", [])}
        self._watched_names = {str(value) for value in cfg.get("watched_names", [])}
        self._min_watched_items = int(cfg.get("min_watched_items", 1))
        self._baseline_file = str(cfg.get("baseline_file", "")).strip()
        self._ignored_dirs = set(_DEFAULT_IGNORED_DIRS) | {
            str(value) for value in cfg.get("ignored_dir_names", [])}
        self._ignored_suffixes = _DEFAULT_IGNORED_SUFFIXES + tuple(
            str(value) for value in cfg.get("ignored_suffixes", []))
        self._scope_digest = self._calculate_scope_digest()
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        self._running = True
        if not self._established and self._baseline_file:
            self._load_release_baseline()
        if self._established:
            # Verify before the atom can report healthy; a shipped baseline is
            # trust material, not a reason to skip the first real scan.
            await self._on_pulse({})

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _calculate_scope_digest(self) -> str:
        payload = {"files": self._watched_files, "dirs": self._watched_dirs,
                   "extensions": sorted(self._watched_extensions),
                   "names": sorted(self._watched_names),
                   "minimum": self._min_watched_items,
                   "ignored_dirs": sorted(self._ignored_dirs),
                   "ignored_suffixes": list(self._ignored_suffixes)}
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _load_release_baseline(self) -> None:
        try:
            with open(self._baseline_file, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, ValueError) as exc:
            self._baseline_error = "BASELINE_READ_FAILED:%s" % type(exc).__name__
            return
        items = payload.get("items") if isinstance(payload, dict) else None
        if (not isinstance(items, dict)
                or payload.get("format") != "quant-nq-integrity-baseline"
                or payload.get("scope_digest") != self._scope_digest):
            self._baseline_error = "BASELINE_SCOPE_MISMATCH"
            return
        self._baseline = {str(key): str(value) for key, value in items.items()}
        self._established = bool(self._baseline)
        self._watched_count = len(self._baseline)
        self._baseline_error = "" if self._established else "BASELINE_EMPTY"

    def _checksum(self, path: str) -> str:
        try:
            return _sha256_of_file(path)
        except FileNotFoundError:
            return _MISSING
        except OSError as exc:
            return _ERROR_PREFIX + str(exc)

    def _allowed(self, name: str) -> bool:
        if name in self._watched_names:
            return True
        if not self._watched_extensions:
            return True
        return os.path.splitext(name)[1].lower() in self._watched_extensions

    def _walk_source(self, dir_path: str) -> list[tuple[str, str]]:
        collected: list[tuple[str, str]] = []
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [name for name in dirs if name not in self._ignored_dirs]
            for name in files:
                if name.endswith(self._ignored_suffixes) or not self._allowed(name):
                    continue
                full = os.path.join(root, name)
                # Owner stamp 2026-08-25: relpath yields the host separator, so
                # the same tree keyed "atoms/x/atom.py" on one OS and
                # "atoms\x\atom.py" on another. A baseline generated elsewhere
                # then matched NOTHING and every file read as "added" (measured:
                # +2233). Keys are normalised to POSIX so the baseline is
                # portable and the guard compares like with like.
                rel = os.path.relpath(full, dir_path).replace(os.sep, "/")
                collected.append((rel, self._checksum(full)))
        return collected

    def _collect(self) -> dict[str, str]:
        current: dict[str, str] = {}
        for path in self._watched_files:
            current["file:" + path] = self._checksum(path)
        for dir_path in self._watched_dirs:
            if not os.path.isdir(dir_path):
                current["dirroot:" + dir_path] = _MISSING
                continue
            for rel, checksum in self._walk_source(dir_path):
                current["dir:" + dir_path + "::" + rel] = checksum
        return current

    def _diff(self, current: dict[str, str]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        for key, value in current.items():
            base = self._baseline.get(key)
            if base is None:
                violations.append({"key": key, "diff_type": "added"})
            elif value != base:
                if value == _MISSING:
                    violations.append({"key": key, "diff_type": "missing"})
                elif value.startswith(_ERROR_PREFIX):
                    violations.append({"key": key, "diff_type": "read_error",
                                       "detail": value[len(_ERROR_PREFIX):]})
                else:
                    violations.append({"key": key, "diff_type": "modified"})
        for key in self._baseline:
            if key not in current:
                violations.append({"key": key, "diff_type": "removed"})
        return violations

    @staticmethod
    def _summarize(violations: list[dict[str, Any]]) -> str:
        shown = violations[:_MAX_SUMMARY_ITEMS]
        text = "; ".join(f"{row['diff_type']}: {row['key']}" for row in shown)
        if len(violations) > _MAX_SUMMARY_ITEMS:
            text += f"; +{len(violations) - _MAX_SUMMARY_ITEMS} more"
        return text

    def _details(self) -> dict[str, Any]:
        return {"watched_items": self._watched_count,
                "min_watched_items": self._min_watched_items,
                "scan_ms": self._scan_ms,
                "established": self._established,
                "baseline_file": self._baseline_file,
                "baseline_error": self._baseline_error,
                "scope_digest": self._scope_digest,
                "violations": list(self._last_violations)}

    def _guard_disabled(self, count: int) -> bool:
        return not self._watched_dirs or count < self._min_watched_items

    async def establish_baseline(self) -> HealthStatus:
        started = time.perf_counter()
        current = self._collect()
        self._scan_ms = (time.perf_counter() - started) * 1000.0
        self._watched_count = len(current)
        if self._guard_disabled(self._watched_count):
            self._last_health = HealthStatus(
                state=HealthState.UNHEALTHY, message="GUARD_DISABLED",
                details=self._details())
            return self._last_health
        self._baseline = current
        self._established = True
        self._baseline_error = ""
        self._last_violations = []
        self._last_health = HealthStatus(
            state=HealthState.HEALTHY,
            message=f"{len(current)} source items explicitly baselined",
            details=self._details())
        return self._last_health

    async def _on_pulse(self, _payload: dict[str, Any]) -> None:
        if not self._running:
            return
        started = time.perf_counter()
        current = self._collect()
        self._scan_ms = (time.perf_counter() - started) * 1000.0
        self._watched_count = len(current)
        if self._guard_disabled(self._watched_count):
            self._last_violations = []
            self._last_health = HealthStatus(
                state=HealthState.UNHEALTHY, message="GUARD_DISABLED",
                details=self._details())
            return
        if not self._established:
            self._last_violations = []
            self._last_health = HealthStatus(
                state=HealthState.UNHEALTHY,
                message=self._baseline_error or "UNTRUSTED",
                details=self._details())
            return
        violations = self._diff(current)
        self._last_violations = violations
        if violations:
            if self._context is not None:
                await self._context.publish(EVENT_VIOLATION, {
                    "violations": violations, "watched_items": len(current),
                    "scan_ms": self._scan_ms})
            self._last_health = HealthStatus(
                state=HealthState.UNHEALTHY,
                message=self._summarize(violations), details=self._details())
            return
        self._last_health = HealthStatus(
            state=HealthState.HEALTHY,
            message=f"{len(current)} source items match baseline",
            details=self._details())

    async def health_check(self) -> HealthStatus:
        return self._last_health

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "baseline": dict(self._baseline),
                "scope_digest": self._scope_digest,
                "established": self._established}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_INTEGRITY_BASELINE")
        baseline = state.get("baseline")
        scope_digest = state.get("scope_digest")
        if (not isinstance(baseline, dict) or scope_digest != self._scope_digest
                or state.get("established") is not True):
            self._baseline = {}
            self._established = False
            self._last_health = HealthStatus(
                state=HealthState.UNHEALTHY, message="UNTRUSTED",
                details=self._details())
            return
        self._baseline = {str(key): str(value) for key, value in baseline.items()}
        self._established = bool(self._baseline)
        self._watched_count = len(self._baseline)
        self._last_health = HealthStatus(
            state=HealthState.DEGRADED, message="RESTORED_PENDING_SCAN",
            details=self._details())
