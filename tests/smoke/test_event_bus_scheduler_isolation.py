import asyncio
import time

from core.event_bus import EventBus


def test_blocking_async_handler_does_not_starve_other_handlers() -> None:
    async def scenario() -> None:
        bus = EventBus(dispatch_timeout_s=2.0, mailbox_max_events=32)
        fast_done = asyncio.Event()

        async def slow(_payload):
            time.sleep(0.20)

        async def fast(_payload):
            fast_done.set()

        bus.subscribe("TEST.SCHEDULER", slow, subscriber="slow")
        bus.subscribe("TEST.SCHEDULER", fast, subscriber="fast")

        started = time.perf_counter()
        await bus.publish("TEST.SCHEDULER", {"value": 1})
        await asyncio.wait_for(fast_done.wait(), timeout=0.50)
        assert time.perf_counter() - started < 0.45
        bus.close()

    asyncio.run(scenario())


def test_worker_publication_returns_to_core_loop() -> None:
    async def scenario() -> None:
        bus = EventBus(dispatch_timeout_s=2.0, mailbox_max_events=32)
        child_seen = asyncio.Event()

        async def child(_payload):
            child_seen.set()

        async def parent(_payload):
            await bus.publish("TEST.CHILD", {"ok": True}, publisher="parent")

        bus.subscribe("TEST.CHILD", child, subscriber="child")
        bus.subscribe("TEST.PARENT", parent, subscriber="parent")

        await bus.publish("TEST.PARENT", {"ok": True})
        await asyncio.wait_for(child_seen.wait(), timeout=0.50)
        bus.close()

    asyncio.run(scenario())


def test_time_signals_are_latest_only() -> None:
    async def scenario() -> None:
        bus = EventBus(dispatch_timeout_s=2.0, mailbox_max_events=32)

        async def slow(_payload):
            await asyncio.sleep(0.05)

        bus.subscribe("SYS_SECOND", slow, subscriber="clock")
        for value in range(20):
            await bus.publish("SYS_SECOND", {"count": value})

        stats = bus.stats()
        assert stats["coalesced"].get("SYS_SECOND", 0) > 0
        bus.close()

    asyncio.run(scenario())


def test_realtime_lane_survives_general_worker_saturation() -> None:
    async def scenario() -> None:
        bus = EventBus(dispatch_timeout_s=2.0, mailbox_max_events=8)
        realtime_seen = asyncio.Event()

        async def slow_general(_payload):
            time.sleep(0.35)

        async def realtime(_payload):
            realtime_seen.set()

        for index in range(40):
            bus.subscribe(f"TEST.LOAD.{index}", slow_general, subscriber=f"load-{index}")

        for index in range(40):
            await bus.publish(f"TEST.LOAD.{index}", {"index": index})

        bus.subscribe("SYS_SECOND", realtime, subscriber="clock")
        started = time.perf_counter()
        await bus.publish("SYS_SECOND", {"count": 1})
        await asyncio.wait_for(realtime_seen.wait(), timeout=0.75)
        assert time.perf_counter() - started < 0.70
        bus.close()

    asyncio.run(scenario())
