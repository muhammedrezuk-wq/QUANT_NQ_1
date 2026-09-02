"""Read-only payload primitives for the low-copy event transport.

These classes intentionally remain ``dict``/``list`` subclasses so existing
atoms keep their current contracts (``isinstance(value, dict)`` continues to
work).  They are built once at ingress and then shared by read-only consumers.
Any attempted mutation fails loudly instead of corrupting another consumer's
view of an event.
"""

from __future__ import annotations

from collections import deque
from typing import Any


class ReadOnlyDict(dict):
    """A recursively read-only dict compatible with the existing atom API."""

    def __init__(self, source: Any = ()) -> None:
        dict.__init__(self)
        if hasattr(source, "items"):
            items = source.items()
        else:
            items = source
        for key, value in items:
            dict.__setitem__(self, key, freeze_payload(value))

    @staticmethod
    def _readonly() -> None:
        raise TypeError("event payload is read-only; create a derived payload instead")

    __setitem__ = _readonly
    __delitem__ = _readonly
    clear = _readonly
    pop = _readonly
    popitem = _readonly
    setdefault = _readonly
    update = _readonly
    __ior__ = _readonly

    def __reduce__(self):
        return (ReadOnlyDict, (dict(self),))

    def __deepcopy__(self, memo: dict[int, Any]):
        clone = ReadOnlyDict()
        memo[id(self)] = clone
        for key, value in self.items():
            dict.__setitem__(clone, key, freeze_payload(value))
        return clone


class ReadOnlyList(list):
    """A recursively read-only list compatible with list consumers."""

    def __init__(self, source: Any = ()) -> None:
        list.__init__(self, (freeze_payload(value) for value in source))

    @staticmethod
    def _readonly(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("event payload is read-only; create a derived payload instead")

    __setitem__ = _readonly
    __delitem__ = _readonly
    __iadd__ = _readonly
    __imul__ = _readonly
    append = _readonly
    clear = _readonly
    extend = _readonly
    insert = _readonly
    pop = _readonly
    remove = _readonly
    reverse = _readonly
    sort = _readonly

    def __reduce__(self):
        return (ReadOnlyList, (list(self),))

    def __deepcopy__(self, memo: dict[int, Any]):
        clone = ReadOnlyList()
        memo[id(self)] = clone
        list.extend(clone, (freeze_payload(value) for value in self))
        return clone


def freeze_payload(value: Any) -> Any:
    """Return one recursively immutable view of JSON-like event data.

    Unknown application objects are retained as-is for compatibility; normal
    event payloads are dict/list/scalar structures.  A deque is converted to a
    read-only list because it is a mutable container and is not part of the
    public event contract.
    """
    if isinstance(value, ReadOnlyDict | ReadOnlyList):
        return value
    if isinstance(value, dict):
        return ReadOnlyDict(value)
    if isinstance(value, list):
        return ReadOnlyList(value)
    if isinstance(value, tuple):
        return tuple(freeze_payload(item) for item in value)
    if isinstance(value, deque):
        return ReadOnlyList(value)
    if isinstance(value, set):
        return frozenset(value)
    return value
