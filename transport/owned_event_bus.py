"""Executable Event Ownership transport for the production runner.

The frozen ``core.event_bus.EventBus`` remains the compatibility implementation.
This adapter keeps its public contract while moving delivery into owned worker
lanes:

* ingress normalizes and copies a payload once;
* default consumers receive one recursively read-only object by reference;
* every Account×Symbol partition has one stable worker owner, preserving FIFO
  ordering for that state;
* unscoped legacy handlers have one stable worker owner;
* worker mailboxes are bounded, fair, coalescing for state events, and visible;
* handler errors/timeouts remain isolated by the original core delivery code;
* the Account×Symbol ownership map is deterministic across restarts;
* explicit ``isolate_payload=True`` remains available for a private consumer.

This is a transport/runtime change only. Event names, atom code, and trading
rules are not changed.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from typing import Any, Callable

from clock import now as official_now
from core.event_bus import EventBus, _fast_copy, _is_replayable
from core.logger import current_event, current_event_id, current_trace_id
from .ownership import OwnershipRuntime
from .readonly import freeze_payload

_log = logging.getLogger("quant_nq.transport.owned_event_bus")


class OwnedEventBus(EventBus):
    """EventBus-compatible transport with owned execution lanes."""

    def __init__(
        self,
        *,
        payload_mode: str = "shared_readonly",
        worker_count: int = 4,
        mailbox_max_events: int = 1024,
        partitioned_ownership: bool = True,
        **kwargs: Any,
    ) -> None:
        mode = str(payload_mode).strip().lower()
        if mode not in {"shared_readonly", "isolated"}:
            raise ValueError("payload_mode must be 'shared_readonly' or 'isolated'")
        super().__init__(mailbox_max_events=mailbox_max_events, **kwargs)
        self.payload_mode = mode
        self.worker_count = max(1, int(worker_count))
        self._cold_handlers: set[int] = set()
        self._published_total = 0
        self._shared_published = 0
        self._shared_deliveries = 0
        self._private_deliveries = 0
        self._runtime = OwnershipRuntime(
            worker_count=self.worker_count,
            mailbox_max_events=mailbox_max_events,
            dispatch=self._dispatch_owned,
            is_active=self._handler_is_active,
            partitioned=partitioned_ownership,
        )

    def _enqueue(self, handler_id: int, item: tuple[Any, ...], event_name: str) -> None:
        """Route one reference into an owned worker mailbox.

        ``EventBus.subscribe`` and ``subscribe_all`` call this method for state
        replay too, so late consumers use the same ordering and supervision
        path as live events.
        """
        if item and item[0] == "sub":
            sub = item[3]
            subscriber = str(getattr(sub, "subscriber", ""))
        else:
            extra = item[3]
            subscriber = str(extra[1]) if len(extra) > 1 else ""
        self._runtime.enqueue(
            handler_id,
            subscriber,
            item[2] if len(item) > 2 else None,
            item,
            event_name,
            cold=handler_id in self._cold_handlers,
        )

    async def _dispatch_owned(self, _handler_id: int, item: tuple[Any, ...]) -> None:
        """Use the original core timeout/error isolation after routing."""
        kind, event_name, payload, extra = item
        if kind == "sub":
            await self._deliver(extra, event_name, payload)
        else:
            handler, subscriber, is_coro = extra
            await self._deliver_global(handler, subscriber, is_coro, event_name, payload)

    def subscribe(
        self,
        event_name: str,
        handler: Callable[..., Any],
        *,
        subscriber: str = "",
        isolate_payload: bool | None = None,
    ) -> None:
        if isolate_payload is None:
            isolate_payload = self.payload_mode != "shared_readonly"
        super().subscribe(
            event_name,
            handler,
            subscriber=subscriber,
            isolate_payload=bool(isolate_payload),
        )

    def subscribe_all(
        self,
        handler: Callable[..., Any],
        *,
        subscriber: str = "",
        isolate_payload: bool | None = None,
    ) -> None:
        if isolate_payload is None:
            isolate_payload = self.payload_mode != "shared_readonly"
        if subscriber.startswith("cold:") or subscriber in {"core.api", "dashboard", "telemetry"}:
            self._cold_handlers.add(id(handler))
        super().subscribe_all(
            handler,
            subscriber=subscriber,
            isolate_payload=bool(isolate_payload),
        )

    async def publish(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
        *,
        publisher: str = "",
    ) -> None:
        if self.payload_mode == "isolated":
            # The parent implementation still routes through our _enqueue
            # override, so this mode retains worker lanes with legacy copies.
            self._published_total += 1
            return await super().publish(event_name, payload, publisher=publisher)

        # One caller-isolation copy only. The normal mutable dict never enters
        # an atom; it is converted to a read-only view before routing.
        raw_base: dict[str, Any] = _fast_copy(payload or {})
        raw_base.setdefault("source", publisher)
        raw_base.setdefault("event_id", str(uuid.uuid4()))
        raw_base.setdefault("trace_id", current_trace_id.get() or str(uuid.uuid4()))
        raw_base.setdefault("parent_event_id", current_event_id.get())
        raw_base.setdefault("parent_event", current_event.get())
        # ٢٠٢٦-٠٨-٣١: لم يعد للناقل ساعة — `EventBus.now()` أُلغيت لأنها كانت
        # مالكًا ثانيًا للوقت. الختم الزمنيّ من السلطة المركزيّة `clock` مباشرة.
        raw_base.setdefault("timestamp", official_now())
        readonly_base = freeze_payload(raw_base)

        self._published_total += 1
        self._published[event_name] += 1
        self._shared_published += 1

        if _is_replayable(event_name):
            # Internal snapshot only. Live consumers receive readonly_base; the
            # inherited late-replay path makes its own safe copy.
            self._last_event[event_name] = raw_base

        subs = list(self._subscribers.get(event_name, ()))
        _log.debug(
            "نشر owned '%s' من '%s' إلى %d مشترك(ين) (trace_id: %s)",
            event_name,
            publisher or "؟",
            len(subs),
            raw_base["trace_id"],
        )

        def private_copy() -> Any:
            self._private_deliveries += 1
            try:
                return _fast_copy(raw_base)
            except Exception:  # pragma: no cover - _fast_copy has its fallback
                return copy.deepcopy(raw_base)

        for handler, subscriber, isolate, is_coro in self._global_subscribers:
            if isolate:
                body = private_copy()
            else:
                body = readonly_base
                self._shared_deliveries += 1
            self._enqueue(
                id(handler),
                ("global", event_name, body, (handler, subscriber, is_coro)),
                event_name,
            )

        if not subs:
            self._no_subscribers[event_name] += 1
            await self._maybe_yield()
            return

        for sub in subs:
            if sub.isolate:
                body = private_copy()
            else:
                body = readonly_base
                self._shared_deliveries += 1
            self._enqueue(
                id(sub.handler),
                ("sub", event_name, body, sub),
                event_name,
            )
        await self._maybe_yield()

    async def drain(self, timeout_s: float | None = None) -> bool:
        return await self._runtime.drain(timeout_s=timeout_s)

    async def close(self) -> None:
        await self._runtime.close()

    async def aclose(self) -> None:
        await self.close()

    def transport_stats(self) -> dict[str, Any]:
        """Raw transport counters and worker/ownership health."""
        return {
            "payload_mode": self.payload_mode,
            "published": self._published_total,
            "shared_publishes": self._shared_published,
            "shared_deliveries": self._shared_deliveries,
            "private_deliveries": self._private_deliveries,
            "runtime": self._runtime.stats(),
        }
