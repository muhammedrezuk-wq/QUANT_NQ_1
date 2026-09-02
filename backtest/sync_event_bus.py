# -*- coding: utf-8 -*-
"""SyncEventBus — ناقل أحداث للباك تست يتعامل مع handlers غير المتزامنة.

ينفذ نفس عقد EventBus (subscribe + publish) لكن بشكل متزامن — بدون asyncio.
الذرّات الفعلية تستعمل async handlers — الناشر يشغّلها فعلياً في event loop.

v2.0 — إصلاح حرج ١: handlers غير المتزامنة تُنفَّذ فعلاً، لا تُبتلع.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger("backtest.sync_bus")


_REUSE_LOOP: asyncio.AbstractEventLoop | None = None


def _reuse_loop() -> asyncio.AbstractEventLoop:
    global _REUSE_LOOP
    if _REUSE_LOOP is None or _REUSE_LOOP.is_closed():
        _REUSE_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_REUSE_LOOP)
    return _REUSE_LOOP


def _run_coro(coro: Any) -> Any:
    """شغّل coroutine — حلقة واحدة معاد استخدامها، لا asyncio.run لكل تيك."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    return _reuse_loop().run_until_complete(coro)


class SyncEventBus:
    """ناقل أحداث متزامن — يشغّل handlers غير المتزامنة فعلياً."""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._all_handlers: list[Callable] = []
        self._events: list[tuple[str, dict, float]] = []  # (name, payload, ts)
        self._dispatch_count: int = 0
        self._error_count: int = 0
        self._errors: list[dict[str, Any]] = []

    def subscribe(self, event_name: str, handler: Callable) -> None:
        """تسجيل مستمع لحدث."""
        self._handlers[event_name].append(handler)

    def subscribe_all(self, handler: Callable) -> None:
        """تسجيل مستمع لكل الأحداث."""
        self._all_handlers.append(handler)

    def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        """نشر حدث — يستدعي كل المستمعين فوراً."""
        ts = time.time()
        safe_payload = dict(payload) if isinstance(payload, dict) else {}
        self._events.append((event_name, safe_payload, ts))
        if len(self._events) > 40_000:
            self._events = self._events[-12_000:]

        # مستمعون محددون
        for handler in self._handlers.get(event_name, []):
            try:
                result = handler(safe_payload)
                if inspect.isawaitable(result):
                    _run_coro(result)
                self._dispatch_count += 1
            except Exception as exc:
                self._error_count += 1
                self._errors.append({
                    "event": event_name,
                    "handler": getattr(handler, "__qualname__", str(handler)),
                    "error": str(exc),
                })
                log.debug(f"SyncBus error in {event_name}: {exc}")

        # مستمعون عامون
        for handler in self._all_handlers:
            try:
                result = handler(event_name, safe_payload)
                if inspect.isawaitable(result):
                    _run_coro(result)
                self._dispatch_count += 1
            except Exception as exc:
                self._error_count += 1
                self._errors.append({
                    "event": event_name,
                    "handler": getattr(handler, "__qualname__", str(handler)),
                    "error": str(exc),
                })

    def get_events(self, event_name: str | None = None) -> list[tuple[str, dict, float]]:
        """قراءة أحداث — كلّي أو حسب الاسم."""
        if event_name is None:
            return list(self._events)
        return [(n, p, t) for n, p, t in self._events if n == event_name]

    def get_last(self, event_name: str) -> dict[str, Any] | None:
        """آخر حدث باسم معين."""
        for n, p, t in reversed(self._events):
            if n == event_name:
                return p
        return None

    def report(self) -> dict[str, Any]:
        return {
            "total_events": len(self._events),
            "total_dispatches": self._dispatch_count,
            "total_errors": self._error_count,
            "subscribed_events": len(self._handlers),
            "total_handlers": sum(len(h) for h in self._handlers.values()),
            "all_handlers": len(self._all_handlers),
        }

    def reset(self) -> None:
        self._events.clear()
        self._dispatch_count = 0
        self._error_count = 0
        self._errors.clear()


def create_logger() -> Any:
    """Logger يطابق LoggerProtocol للنواة."""
    class _Logger:
        def debug(self, msg, *a, **kw): pass
        def info(self, msg, *a, **kw): pass
        def warning(self, msg, *a, **kw): pass
        def error(self, msg, *a, **kw): pass
        def critical(self, msg, *a, **kw): pass
    return _Logger()
