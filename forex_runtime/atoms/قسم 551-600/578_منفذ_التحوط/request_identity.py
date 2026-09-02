"""The part of a request id that must differ across a reload.

Owner's ruling 2026-08-14 (problem 63, option a-2):

    request_id = ...-{snapshot_id}-{official_time}-{n}

`_counter` restarts at 0 on a plain HOT RELOAD of 578 while a consumer such as
585 keeps its holds, so the counter alone can never tell one instance from the
next.  Two values 578 already receives can:

  * `snapshot_id` from 583, whose own version advances independently of 578,
  * `official_time`, which arrives on the bus.

His order is snapshot first, time second.  With BOTH missing the order is
REFUSED -- it is never sent on the counter alone.

NOT ABSOLUTE, and it is not presented as such: if 583 and 578 are reloaded
together, on the same snapshot version and inside the same second, the identity
can still repeat.  That case is written down in paper 60, not hidden.

583 joins account and symbol with \\x1f, so the token is reduced to plain
characters before it can reach the bridge or the EA.  Nothing here parses 583's
format -- the whole value is carried opaquely, so 583 may change it freely.
"""
from __future__ import annotations

from typing import Any

NO_SNAPSHOT = "nosnap"
NO_TIME = "notime"
_KEEP = frozenset("abcdefghijklmnopqrstuvwxyz"
                  "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def _token(value: Any) -> str:
    """Whatever arrived, made safe to carry -- never interpreted."""
    return "".join(character for character in str(value or "") if character in _KEEP)


def request_identity(snapshot_id: Any, official_time: Any) -> str | None:
    """Returns None when nothing distinguishes this instance: fail-closed."""
    snapshot = _token(snapshot_id)
    try:
        stamp = "" if official_time is None else "%d" % int(float(official_time))
    except (TypeError, ValueError):
        stamp = ""
    if not snapshot and not stamp:
        return None
    return "%s-%s" % (snapshot or NO_SNAPSHOT, stamp or NO_TIME)
