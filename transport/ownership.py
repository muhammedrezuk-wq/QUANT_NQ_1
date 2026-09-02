"""Event Ownership runtime primitives.

The runtime separates ingress/routing from handler execution while preserving
FIFO ordering for each handler.  It is intentionally independent of the
trading atoms: the existing EventBus adapter can adopt it without changing
atom logic or event names.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.event_bus import _coalesce_key, _is_command, _is_replayable


@dataclass(frozen=True, slots=True)
class OwnershipKey:
    account_id: str
    symbol: str

    @property
    def text(self) -> str:
        return f"{self.account_id}×{self.symbol}"


def ownership_key(payload: Any) -> OwnershipKey | None:
    if not isinstance(payload, dict):
        return None
    account = payload.get("account_id") or payload.get("account")
    symbol = payload.get("symbol") or payload.get("asset")
    if account is None or symbol is None:
        return None
    account_text = str(account).strip()
    symbol_text = str(symbol).strip().upper()
    if not account_text or not symbol_text:
        return None
    return OwnershipKey(account_text, symbol_text)


class OwnershipRegistry:
    """Deterministic Account×Symbol → Worker ownership map.

    A stable digest is used instead of Python's randomized ``hash`` so restarts
    keep the same placement.  ``reassign`` is explicit and therefore a
    future state-transfer protocol can be added without silently moving live
    state.
    """

    def __init__(self, worker_count: int) -> None:
        self.worker_count = max(1, int(worker_count))
        self._owners: dict[OwnershipKey, int] = {}

    def owner_for(self, key: OwnershipKey | None) -> int:
        if key is None:
            return 0
        current = self._owners.get(key)
        if current is not None:
            return current
        digest = hashlib.blake2b(key.text.encode("utf-8"), digest_size=8).digest()
        current = int.from_bytes(digest, "big") % self.worker_count
        self._owners[key] = current
        return current

    def reassign(self, key: OwnershipKey, worker_id: int) -> None:
        worker_id = int(worker_id)
        if not 0 <= worker_id < self.worker_count:
            raise ValueError(f"worker_id must be between 0 and {self.worker_count - 1}")
        self._owners[key] = worker_id

    def snapshot(self) -> dict[str, int]:
        return {key.text: worker for key, worker in self._owners.items()}


@dataclass(slots=True)
class _HandlerMailbox:
    queue: deque[tuple[Any, ...]]
    ready: bool = False
    busy: bool = False


class OwnershipWorker:
    """One owned execution lane with fair per-handler mailboxes."""

    def __init__(
        self,
        worker_id: int,
        *,
        max_events: int,
        dispatch: Callable[[int, tuple[Any, ...]], Awaitable[None]],
        is_active: Callable[[int], bool],
    ) -> None:
        self.worker_id = worker_id
        self.max_events = max(1, int(max_events))
        self._dispatch = dispatch
        self._is_active = is_active
        self._mailboxes: dict[int, _HandlerMailbox] = {}
        self._ready: deque[int] = deque()
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._busy = 0
        self.processed = 0
        self.dropped = 0
        self.coalesced = 0
        self.failed = 0
        self.last_error = ""
        self.last_activity = 0.0

    def _ensure_task(self) -> None:
        if self._stopping:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run(), name=f"quant-owner-{self.worker_id}")

    def enqueue(self, handler_id: int, item: tuple[Any, ...], event_name: str) -> None:
        mailbox = self._mailboxes.get(handler_id)
        if mailbox is None:
            mailbox = self._mailboxes[handler_id] = _HandlerMailbox(deque())

        # Preserve the existing latest-state policy, now scoped inside the
        # owner's mailbox instead of a central EventBus mailbox.
        if _is_replayable(event_name):
            key = _coalesce_key(event_name, item[2])
            for index in range(len(mailbox.queue) - 1, -1, -1):
                pending = mailbox.queue[index]
                if (pending[1] == event_name
                        and _coalesce_key(event_name, pending[2]) == key):
                    mailbox.queue[index] = item
                    self.coalesced += 1
                    self._ready_handler(handler_id, mailbox)
                    return

        if not _is_command(event_name):
            while len(mailbox.queue) >= self.max_events:
                mailbox.queue.popleft()
                self.dropped += 1
        mailbox.queue.append(item)
        self._ready_handler(handler_id, mailbox)
        self._ensure_task()

    def _ready_handler(self, handler_id: int, mailbox: _HandlerMailbox) -> None:
        if not mailbox.ready:
            mailbox.ready = True
            self._ready.append(handler_id)
        self._wakeup.set()

    async def _run(self) -> None:
        try:
            while not self._stopping:
                if not self._ready:
                    self._wakeup.clear()
                    await self._wakeup.wait()
                    continue

                handler_id = self._ready.popleft()
                mailbox = self._mailboxes.get(handler_id)
                if mailbox is None:
                    continue
                mailbox.ready = False
                if not mailbox.queue:
                    continue

                item = mailbox.queue.popleft()
                if mailbox.queue:
                    self._ready_handler(handler_id, mailbox)
                mailbox.busy = True
                self._busy += 1
                try:
                    # Unsubscribed handlers are retired without executing stale
                    # work. This is the same safety boundary as the old bus.
                    if self._is_active(handler_id):
                        await self._dispatch(handler_id, item)
                        self.processed += 1
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001
                    # A broken dispatch must not kill a whole owner lane.
                    self.failed += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"
                finally:
                    mailbox.busy = False
                    self._busy -= 1
                    self.last_activity = time.perf_counter()
        except asyncio.CancelledError:
            return

    async def drain(self, timeout_s: float | None = None) -> bool:
        deadline = time.monotonic() + timeout_s if timeout_s is not None else None
        while True:
            pending = self._busy > 0 or any(
                mailbox.queue for mailbox in self._mailboxes.values()
            )
            if not pending:
                return True
            if deadline is not None and time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.001)

    async def close(self) -> None:
        self._stopping = True
        self._wakeup.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._mailboxes.clear()
        self._ready.clear()

    def stats(self) -> dict[str, Any]:
        queued = sum(len(mailbox.queue) for mailbox in self._mailboxes.values())
        busy = sum(1 for mailbox in self._mailboxes.values() if mailbox.busy)
        if queued == 0 and busy == 0:
            state = "NORMAL"
        elif queued < self.max_events:
            state = "DEGRADED"
        else:
            state = "OVERLOAD"
        return {
            "worker_id": self.worker_id,
            "queued": queued,
            "busy_handlers": busy,
            "processed": self.processed,
            "dropped": self.dropped,
            "coalesced": self.coalesced,
            "failed": self.failed,
            "overload_state": state,
            "last_error": self.last_error,
        }


class OwnershipRuntime:
    """Supervisor for independent owner lanes and the ownership registry."""

    def __init__(
        self,
        *,
        worker_count: int,
        mailbox_max_events: int,
        dispatch: Callable[[int, tuple[Any, ...]], Awaitable[None]],
        is_active: Callable[[int], bool],
        partitioned: bool = True,
    ) -> None:
        self.worker_count = max(1, int(worker_count))
        self.partitioned = bool(partitioned)
        self.registry = OwnershipRegistry(self.worker_count)
        self._dispatch = dispatch
        self._is_active = is_active
        self._handler_workers: dict[int, int] = {}
        self._cold_handlers: set[int] = set()
        self._workers = [
            OwnershipWorker(
                worker_id,
                max_events=mailbox_max_events,
                dispatch=dispatch,
                is_active=is_active,
            )
            for worker_id in range(self.worker_count)
        ]
        self._cold_worker = self.worker_count - 1
        self._closed = False

    def register_handler(self, handler_id: int, subscriber: str, *, cold: bool = False) -> int:
        if cold:
            self._cold_handlers.add(handler_id)
            self._handler_workers[handler_id] = self._cold_worker
        elif handler_id not in self._handler_workers:
            raw = str(subscriber or handler_id).encode("utf-8")
            digest = hashlib.blake2b(raw, digest_size=8).digest()
            self._handler_workers[handler_id] = int.from_bytes(digest, "big") % self.worker_count
        return self._handler_workers[handler_id]

    def route(self, handler_id: int, subscriber: str, payload: Any, *, cold: bool = False) -> int:
        # A cold consumer has an explicit output owner. For a keyed hot event,
        # the Account×Symbol owner wins; this is the target architecture and
        # guarantees all events for one state partition stay on one lane. Events
        # without a scope keep a stable handler owner for legacy/global atoms.
        if cold:
            self.register_handler(handler_id, subscriber, cold=True)
            return self._cold_worker
        key = ownership_key(payload)
        if self.partitioned and key is not None:
            return self.registry.owner_for(key)
        return self.register_handler(handler_id, subscriber, cold=False)

    def enqueue(
        self,
        handler_id: int,
        subscriber: str,
        payload: Any,
        item: tuple[Any, ...],
        event_name: str,
        *,
        cold: bool = False,
    ) -> None:
        if self._closed:
            return
        worker_id = self.route(handler_id, subscriber, payload, cold=cold)
        self._workers[worker_id].enqueue(handler_id, item, event_name)

    async def drain(self, timeout_s: float | None = None) -> bool:
        results = await asyncio.gather(*(
            worker.drain(timeout_s=timeout_s) for worker in self._workers
        ))
        return all(results)

    async def close(self) -> None:
        self._closed = True
        await asyncio.gather(*(worker.close() for worker in self._workers))

    def stats(self) -> dict[str, Any]:
        return {
            "worker_count": self.worker_count,
            "partitioned": self.partitioned,
            "cold_worker": self._cold_worker,
            "ownership": self.registry.snapshot(),
            "workers": [worker.stats() for worker in self._workers],
        }
