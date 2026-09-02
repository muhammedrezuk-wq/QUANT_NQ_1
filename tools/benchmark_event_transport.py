"""Quick local comparison of isolated vs shared-readonly event transport."""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.event_bus import EventBus  # noqa: E402
from transport.owned_event_bus import OwnedEventBus  # noqa: E402


async def run(bus, publishes: int, subscribers: int) -> float:
    async def reader(payload: dict) -> None:
        payload.get("sequence")

    for index in range(subscribers):
        bus.subscribe("market.tick", reader, subscriber=str(index))
    payload = {
        "account_id": "A",
        "symbol": "NQ",
        "sequence": 1,
        "rows": [{"i": i, "values": [i, i + 1, i + 2]} for i in range(60)],
    }
    started = time.perf_counter()
    for _ in range(publishes):
        await bus.publish("market.tick", payload)
    elapsed = time.perf_counter() - started
    await bus.drain(timeout_s=30.0)
    return elapsed


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publishes", type=int, default=300)
    parser.add_argument("--subscribers", type=int, default=32)
    args = parser.parse_args()
    for name, bus in (("isolated", EventBus()),
                      ("shared_readonly", OwnedEventBus())):
        elapsed = await run(bus, args.publishes, args.subscribers)
        print(f"{name}: {args.publishes / elapsed:.0f} publishes/s ({elapsed:.4f}s)")
        if hasattr(bus, "transport_stats"):
            print(bus.transport_stats())


if __name__ == "__main__":
    asyncio.run(main())
