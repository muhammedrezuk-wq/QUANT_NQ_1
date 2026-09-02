"""Spread barrier for the last gate before the broker -- paper 90-3.

Measured live 2026-08-16: `feed.mt5.tick` carries bid/ask and NO precomputed
spread, so a gate expecting `spread_pts` found "unknown" every time and refused
six orders. Fail-closed was right; the expectation was wrong. The spread is
derived from the two prices here.

Kept out of the atom body only because the atom hit its size limit -- the rule
is still one atom, one job.
"""
from __future__ import annotations

from typing import Any


def spread_points(payload: Any, spec: Any) -> float | None:
    """Spread in points, or None when it genuinely cannot be measured."""
    if not isinstance(payload, dict):
        return None
    ready = payload.get("spread_pts", payload.get("spread_points"))
    if ready is not None:
        try:
            value = float(ready)
        except (TypeError, ValueError):
            return None
        return value if value == value else None

    try:
        gap = float(payload["ask"]) - float(payload["bid"])
    except (KeyError, TypeError, ValueError):
        return None
    if gap != gap:
        return None
    point = (spec or {}).get("point") or (spec or {}).get("tick_size")
    try:
        size = float(point)
    except (TypeError, ValueError):
        return gap
    return gap / size if size > 0.0 else gap


def too_wide(spread: float | None, limit: float) -> bool:
    """Unknown is NOT permission: an unmeasured spread blocks when armed."""
    if limit <= 0.0:
        return False                      # الحدّ صفر = الحاجز معطَّل عمدًا
    return spread is None or spread > limit
