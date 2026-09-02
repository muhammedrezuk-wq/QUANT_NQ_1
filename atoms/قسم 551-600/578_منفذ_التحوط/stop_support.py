"""Last-resort broker stop for a perpetual leg.

Owner's ruling 2026-08-13 (problem 2, option c): the asset budget stays the
risk MANAGER (518 -> 519 -> 581) and the broker stop comes back only as a LAST
RESORT.  It therefore sits at `multiple` x the budget's own working distance,
so the budget always trips first and the broker fires only if the system itself
is dead.

The fallback is his explicit condition: when the position is perfectly hedged
(v_net = 0) neither 512 nor 525 can derive a stop price and 581 hands over no
capacity fraction.  That case takes a DECLARED fixed fraction -- never a silent
None, because an OPEN must never reach the broker naked.
"""
from __future__ import annotations

STOP_FROM_CAPACITY = "CATASTROPHE_FROM_CAPACITY"
STOP_FROM_FALLBACK = "CATASTROPHE_FALLBACK_FRACTION"
BUY = "BUY"


def catastrophe_stop(price: float | None, stop_frac: float | None, side: str,
                     multiple: float, fallback_frac: float) -> tuple | None:
    """(stop_price, working_distance, catastrophe_distance, source) or None.

    None means the stop is not computable at all -- and the caller must then
    send NOTHING, never an order without a stop.
    """
    if price is None or price <= 0.0:
        return None
    if stop_frac is not None and stop_frac > 0.0:
        source = STOP_FROM_CAPACITY
    else:
        stop_frac, source = fallback_frac, STOP_FROM_FALLBACK
    if stop_frac is None or stop_frac <= 0.0:
        return None
    working = price * stop_frac
    distance = working * multiple
    stop = price - distance if side == BUY else price + distance
    if stop <= 0.0:  # a fraction wide enough to cross zero is not a stop
        return None
    return stop, working, distance, source
