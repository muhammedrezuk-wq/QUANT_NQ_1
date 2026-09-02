"""Delta-failure visibility -- owner's ruling 2026-08-14 (problem 58, option a).

MEASUREMENT ONLY.  Nothing in this file decides whether an order is sent,
retried or escalated.  It exists because a failed delta was invisible: the
health details were byte-identical before and after a rejection.

Two counts are reported side by side and never merged:

  delta_failed   -- every failure that reaches the atom, rejections included.
  guard_failing  -- the flood guard's OWN count, the one that actually imposes
                    the backoff, and which a rejection never increments.

Keeping them apart is the whole point: the difference between them IS the gap.
"""
from __future__ import annotations

from typing import Any

MAX_BACKOFF_S = 600.0
_MAX_DOUBLINGS = 16


class DeltaFailures:
    """A tally of failures that no machinery of ours reacts to."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._reasons: dict[str, str] = {}

    def record(self, key: str, reason: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1
        self._reasons[key] = reason

    @staticmethod
    def backoff_s(hold_s: float, failures: int) -> float:
        """The wait the flood guard already imposes -- reported, not imposed.

        Mirrors flood_guard.allows exactly, and that file is not touched: this
        reads its published count and says out loud what it means in seconds.
        """
        if failures <= 0:
            return 0.0
        return min(MAX_BACKOFF_S, float(hold_s) * (2.0 ** min(int(failures), _MAX_DOUBLINGS)))

    def view(self, guard: Any, hold_s: float) -> dict[str, Any]:
        failing = sorted(key for key, count in self._counts.items() if count > 0)
        guard_counts: dict[str, int] = {}
        for key in failing:
            account, _, symbol = key.partition("|")
            guard_counts[key] = int(guard.failing(account, symbol))
        return {
            "delta_failed": sum(self._counts.values()),
            "delta_failing": failing,
            "delta_last_reason": dict(sorted(self._reasons.items())),
            "guard_failing": guard_counts,
            "guard_backoff_s": {key: round(self.backoff_s(hold_s, count), 3)
                                for key, count in sorted(guard_counts.items())},
        }

    @property
    def total(self) -> int:
        return sum(self._counts.values())
