"""Process-wide official clock outside the sealed Core.

Only atom 003 is allowed to call ``accept_sample``. Readers (806/111 and
business atoms) consume the singleton through the read-only module functions.
Timeouts use ``mono``; event timestamps use non-decreasing ``now``.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

SYNCED = "SYNCED"
STALE = "STALE"
LOCAL_FALLBACK = "LOCAL_FALLBACK"
INVALID = "INVALID"
_WRITER_ID = "003"
_EPSILON = 1e-9

@dataclass(slots=True)
class ClockConfig:
    max_accepted_offset_s: float = 5.0
    max_sample_age_s: float = 30.0
    stale_after_s: float = 900.0
    max_slew_per_second: float = 0.05

class OfficialClock:
    def __init__(self, wall: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._wall = wall; self._monotonic = monotonic
        self._lock = threading.RLock(); self._config = ClockConfig()
        self._effective_offset = 0.0; self._target_offset = 0.0
        self._last_slew_mono = monotonic(); self._accepted_mono: float | None = None
        self._measured_at: float | None = None; self._last_official: float | None = None
        self._sequence = 0; self._last_rejection = ""; self._backward_clamps = 0
        self._last_sample_id: Any = None

    def configure(self, *, max_accepted_offset_s: float, max_sample_age_s: float,
                  stale_after_s: float, max_slew_per_second: float) -> None:
        values = (max_accepted_offset_s, max_sample_age_s, stale_after_s, max_slew_per_second)
        if not all(isinstance(v, (int, float)) and math.isfinite(float(v)) and float(v) > 0 for v in values):
            raise ValueError("invalid clock configuration")
        with self._lock:
            self._config = ClockConfig(*(float(v) for v in values))

    def mono(self) -> float: return self._monotonic()

    def _slew_locked(self, mono_now: float) -> None:
        elapsed = max(0.0, mono_now - self._last_slew_mono)
        allowance = elapsed * self._config.max_slew_per_second
        delta = self._target_offset - self._effective_offset
        if abs(delta) <= allowance: self._effective_offset = self._target_offset
        elif allowance > 0: self._effective_offset += math.copysign(allowance, delta)
        self._last_slew_mono = mono_now

    def now(self) -> float:
        with self._lock:
            mono_now = self._monotonic(); self._slew_locked(mono_now)
            candidate = self._wall() + self._effective_offset
            if not math.isfinite(candidate):
                self._last_rejection = "NON_FINITE_WALL_CLOCK"
                candidate = self._last_official if self._last_official is not None else 0.0
            if self._last_official is not None and candidate < self._last_official:
                self._backward_clamps += 1
                self._last_rejection = "BACKWARD_WALL_CLOCK"
                candidate = self._last_official
            self._last_official = candidate
            return candidate

    def quality(self) -> str:
        # Refresh the public reading before classifying it so a wall-clock
        # rollback cannot slip through an exposure gate merely because no
        # pulse happened between the rollback and the order.
        self.now()
        with self._lock:
            if self._last_rejection in ("NON_FINITE_WALL_CLOCK", "BACKWARD_WALL_CLOCK"):
                return INVALID
            if self._accepted_mono is None: return LOCAL_FALLBACK
            age = self._monotonic() - self._accepted_mono
            return SYNCED if 0 <= age <= self._config.stale_after_s else STALE

    def accept_sample(self, sample: dict[str, Any], *, writer: str) -> tuple[bool, str]:
        if writer != _WRITER_ID: return False, "WRITER_NOT_AUTHORIZED"
        try:
            offset = float(sample["median_offset_s"]); measured_at = float(sample["measured_at"])
        except (KeyError, TypeError, ValueError): return False, "MALFORMED_SAMPLE"
        if not math.isfinite(offset) or not math.isfinite(measured_at): return False, "NON_FINITE_SAMPLE"
        wall_now = self._wall(); age = wall_now - measured_at
        if age < -1.0 or age > self._config.max_sample_age_s: return False, "STALE_OR_FUTURE_SAMPLE"
        if abs(offset) > self._config.max_accepted_offset_s: return False, "OFFSET_OUTSIDE_BOUND"
        if sample.get("quorum") is not True: return False, "NO_QUORUM"
        sample_id = sample.get("sample_id")
        with self._lock:
            if sample_id is not None and sample_id == self._last_sample_id:
                return False, "DUPLICATE_SAMPLE"
            mono_now = self._monotonic(); self._slew_locked(mono_now)
            self._target_offset = offset; self._accepted_mono = mono_now
            self._measured_at = measured_at; self._last_sample_id = sample_id
            self._sequence += 1; self._last_rejection = ""
        self.now()  # establish/clamp the public value immediately
        return True, "ACCEPTED"

    def state(self) -> dict[str, Any]:
        with self._lock:
            mono_now = self._monotonic(); self._slew_locked(mono_now)
            age = None if self._accepted_mono is None else max(0.0, mono_now - self._accepted_mono)
            return {"offset_s": self._effective_offset, "target_offset_s": self._target_offset,
                "sync_age_s": age, "quality": self.quality(), "sequence": self._sequence,
                "measured_at": self._measured_at, "monotonic_time": mono_now,
                "backward_clamps": self._backward_clamps,
                "last_rejection": self._last_rejection}

    def reset_for_tests(self) -> None:
        with self._lock:
            self._effective_offset = self._target_offset = 0.0
            self._last_slew_mono = self._monotonic(); self._accepted_mono = None
            self._measured_at = self._last_official = None; self._sequence = 0
            self._last_rejection = ""; self._backward_clamps = 0; self._last_sample_id = None

_CLOCK = OfficialClock()
def now() -> float: return _CLOCK.now()
def mono() -> float: return _CLOCK.mono()
def quality() -> str: return _CLOCK.quality()
def state() -> dict[str, Any]: return _CLOCK.state()
def configure(**kwargs) -> None: _CLOCK.configure(**kwargs)
def accept_sample(sample: dict[str, Any], *, writer: str) -> tuple[bool, str]:
    return _CLOCK.accept_sample(sample, writer=writer)
def reset_for_tests() -> None: _CLOCK.reset_for_tests()
