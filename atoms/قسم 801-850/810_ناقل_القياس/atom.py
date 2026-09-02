# -*- coding: utf-8 -*-
"""Telemetry Carrier (810) — intelligence paper Phase 2.

One passive listener on the event firehose (``subscribe_all``) that buffers
slim telemetry rows and flushes them to batched JSONL files — NEVER on the
hot path:

  * the handler is O(1): one append to a bounded deque, nothing awaited;
  * the buffer sheds before the system does (§30 priorities): overflow is
    counted with a reason code, never blocking;
  * writes go to ``var/telemetry/`` — a path of its own, never ``var/store``
    (production stores are untouchable, paper §7);
  * rows are SLIM: event name + causal identity + source — the raw payload is
    NOT copied into telemetry (the market store already records data).

Read-only covenant: subscribed with ``isolate_payload=False`` — this atom
never mutates a payload.
"""

from __future__ import annotations

import gzip
import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

# Named defaults (Article 9: no magic numbers) -- every value is a dial.
DEFAULT_BATCH_SIZE = 500
DEFAULT_FLUSH_INTERVAL_S = 30.0
DEFAULT_MAX_FILES = 24
DEFAULT_MAX_BUFFER = 20_000

EVENT_BATCH = "telemetry.batch.closed"
EVENT_STATE = "telemetry.carrier.state"
EVENT_TICK = "market.tick.validated"

DEFAULT_PREFIXES = ("market.", "analysis.", "structure.", "liquidity.",
                    "stats.", "probability.", "strategy.", "decision.",
                    "risk.", "execution.", "feed.", "replay.")

REASON_NOT_STARTED = "NOT_STARTED"
REASON_SILENT = "NO_TELEMETRY_YET"


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._out_dir = Path("var/telemetry")
        self._prefixes: tuple[str, ...] = DEFAULT_PREFIXES
        self._batch_size = DEFAULT_BATCH_SIZE
        self._flush_interval_s = DEFAULT_FLUSH_INTERVAL_S
        self._max_files = DEFAULT_MAX_FILES
        self._buffer: deque[dict[str, Any]] = deque()
        self._max_buffer = DEFAULT_MAX_BUFFER
        self._rows_seen = 0
        self._batches = 0
        self._rows_written = 0
        self._last_flush = 0.0
        # §Build 3: every drop is counted with a reason code.
        self._dropped = 0
        self._drop_reasons: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._out_dir = Path(str(cfg.get("out_dir") or "var/telemetry"))
        self._batch_size = max(1, int(cfg.get("batch_size") or DEFAULT_BATCH_SIZE))
        self._flush_interval_s = max(0.0, float(cfg.get("flush_interval_s") or DEFAULT_FLUSH_INTERVAL_S))
        self._max_files = max(1, int(cfg.get("max_files") or DEFAULT_MAX_FILES))
        self._max_buffer = max(self._batch_size, int(cfg.get("max_buffer") or DEFAULT_MAX_BUFFER))
        prefixes = cfg.get("stream_prefixes")
        self._prefixes = tuple(str(p) for p in prefixes) if prefixes else DEFAULT_PREFIXES
        # 1.14.0 optional service. Bootloader signature (rule 19: identity
        # comes from the manifest id, the core sets subscriber itself):
        #   subscribe_all = lambda handler, _aid=atom_id: bus.subscribe_all(
        #       handler, subscriber=str(_aid))
        # So the atom passes ONLY the handler -- passing subscriber= or
        # isolate_payload= raised "unexpected keyword argument" at live boot
        # (measured on the owner's machine 2026-08-23). The read-only covenant
        # now holds by receiving the core's isolated copy -- safer still.
        firehose = getattr(context, "subscribe_all", None)
        if firehose is not None:
            firehose(self._on_any)
        else:
            self._drop("FIREHOSE_UNAVAILABLE")
        context.subscribe("SYS_SECOND", self._on_second)

    async def start(self) -> None:
        self._running = True
        self._last_flush = time.monotonic()
        await self._publish_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    async def _on_any(self, event_name: str, payload: Any) -> None:
        """Firehose row — O(1), nothing awaited, never blocks the publisher."""
        if not self._running or not isinstance(payload, dict):
            return
        if not event_name.startswith(self._prefixes):
            return
        self._rows_seen += 1
        row = {"ts": payload.get("timestamp"), "event": event_name,
               "source": payload.get("source"),
               "trace_id": payload.get("trace_id"),
               "event_id": payload.get("event_id"),
               "parent_event_id": payload.get("parent_event_id"),
               "symbol": payload.get("symbol"),
               "cycle_id": payload.get("cycle_id")}
        if len(self._buffer) >= self._max_buffer:
            # §30: telemetry sheds before the hot path ever feels pressure.
            self._buffer.popleft()
            self._drop("TELEMETRY_BUFFER_FULL")
        self._buffer.append(row)

    async def _on_second(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        elapsed = time.monotonic() - self._last_flush
        if len(self._buffer) >= self._batch_size or (
                self._buffer and elapsed >= self._flush_interval_s):
            await self._flush()

    async def _publish_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_STATE, {
            "id": "telemetry_carrier",
            "state": "HEALTHY" if self._rows_seen else "DEGRADED",
            "rows_seen": self._rows_seen,
            "batches": self._batches,
            "rows_written": self._rows_written,
            "buffered": len(self._buffer),
            "dropped": self._dropped,
        })

    async def _flush(self) -> None:
        if self._context is None or not self._buffer:
            return
        self._last_flush = time.monotonic()
        rows = list(self._buffer)
        self._buffer.clear()
        day = time.strftime("%Y%m%d")
        self._out_dir.mkdir(parents=True, exist_ok=True)
        (self._out_dir / day).mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self._out_dir / day / f"telemetry-{stamp}-{self._batches}.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._batches += 1
        self._rows_written += len(rows)
        self._rotate(day)
        await self._context.publish(EVENT_BATCH, {
            "rows": len(rows), "file": str(path), "batches": self._batches,
            "dropped": self._dropped,
            "drop_reasons": dict(self._drop_reasons)})
        await self._publish_state()

    def _rotate(self, day: str) -> None:
        files = sorted((self._out_dir / day).glob("telemetry-*.jsonl.gz"))
        while len(files) > self._max_files:
            files.pop(0).unlink(missing_ok=True)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"rows_seen": self._rows_seen, "batches": self._batches,
                   "rows_written": self._rows_written,
                   "buffered": len(self._buffer), "dropped": self._dropped,
                   "drop_reasons": dict(self._drop_reasons)}
        if not self._rows_seen:
            return HealthStatus(state=HealthState.DEGRADED,
                                message=REASON_SILENT, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message=f"rows={self._rows_seen} batches={self._batches}",
                            details=details)
