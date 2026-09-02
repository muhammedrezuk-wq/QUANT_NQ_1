import asyncio
import inspect
import os
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom720", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom720"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_TRADE = _mod.EVENT_TRADE
EVENT_APPEARED = _mod.EVENT_APPEARED
EVENT_VANISHED = _mod.EVENT_VANISHED
EVENT_REJECTED = _mod.EVENT_REJECTED
EVENT_ACK = _mod.EVENT_ACK
EVENT_CMD_FAILED = _mod.EVENT_CMD_FAILED
EVENT_STATE = _mod.EVENT_STATE


class _NullLogger:
    def debug(self, *a): pass
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass
    def critical(self, *a): pass


class FakeEventBus:
    def __init__(self):
        self.published = []
        self._handlers = {}

    def subscribe(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))
        for h in self._handlers.get(name, []):
            r = h(payload)
            if inspect.isawaitable(r):
                await r

    def make_context(self, cfg):
        return AtomContext(atom_id=720, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _cfg(tmp, **over):
    cfg = {"dir": tmp, "file_prefix": "trades", "state_tail": 12,
           "max_lines_per_day": 20000}
    cfg.update(over)
    return cfg


def _today_file(tmp):
    return Path(tmp) / ("trades-%s.log" % time.strftime("%Y%m%d"))


async def _run(tmp, **over):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(_cfg(tmp, **over)))
    await atom.start()
    return bus, atom


async def test_opened_line_and_state_event():
    print("\n--- test_opened_line_and_state_event ---")
    tmp = tempfile.mkdtemp()
    bus, _ = await _run(tmp)
    await bus.publish(EVENT_TRADE, {
        "event_type": "OPENED", "ticket": 12345, "symbol": "NQ100",
        "side": "BUY", "volume": 0.1, "entry_price": 23456.5,
        "account_id": "5039911", "source_row_id": 1, "timestamp": time.time()})
    text = _today_file(tmp).read_text(encoding="utf-8")
    assert "- ASMAR TRADE LOG -" in text.splitlines()[0], text
    assert "TRADE_OPENED: BUY NQ100" in text and "volume 0.1" in text, text
    assert "@ 23456.5" in text and "ticket 12345" in text and "account 5039911" in text, text
    states = [p for n, p in bus.published if n == EVENT_STATE]
    assert states and states[-1]["lines_today"] == 1 and states[-1]["kind"] == "opened", states
    assert states[-1]["last_lines"][-1].endswith("account 5039911"), states[-1]
    print("OK — سطر فتح صفقة عربي كامل + حدث حالة logs.trades.state بآخر الأسطر")


async def test_closed_then_cost_revision():
    print("\n--- test_closed_then_cost_revision ---")
    tmp = tempfile.mkdtemp()
    bus, _ = await _run(tmp)
    base = {"event_type": "CLOSED", "ticket": 777, "symbol": "BTCUSD",
            "side": "SELL", "volume": 0.02, "entry_price": 118000,
            "exit_price": 117500.5, "profit": 9.99, "reason": "TP",
            "account_id": "5039911", "source_row_id": 44, "timestamp": time.time()}
    await bus.publish(EVENT_TRADE, base)
    await bus.publish(EVENT_TRADE, {**base, "commission": -0.35, "swap": 0.0, "fee": 0.0})
    lines = _today_file(tmp).read_text(encoding="utf-8").splitlines()
    closed = [l for l in lines if "TRADE_CLOSED: SELL BTCUSD" in l]
    costs = [l for l in lines if "TRADE_COSTS_COMPLETED" in l]
    assert len(closed) == 1 and "profit +9.99" in closed[0] and "reason: TP" in closed[0], lines
    assert "entry 118000" in closed[0] and "exit 117500.5" in closed[0], closed
    assert len(costs) == 1 and "commission -0.35" in costs[0] and "ticket 777" in costs[0], lines
    print("OK — إغلاق يُكتب مرّة، وإعادة نشر التكاليف تصير سطر «اكتملت تكاليف» لا تكرارًا")


async def test_rejected_ack_failed():
    print("\n--- test_rejected_ack_failed ---")
    tmp = tempfile.mkdtemp()
    bus, _ = await _run(tmp)
    now = time.time()
    await bus.publish(EVENT_REJECTED, {
        "symbol": "NQ100", "side": "BUY", "volume": 0.1,
        "reason": "KILL_SWITCH_ACTIVE", "request_id": "a1b2", "timestamp": now})
    await bus.publish(EVENT_ACK, {
        "request_id": "c3d4", "action": "OPEN", "symbol": "NQ100", "side": "BUY",
        "volume": 0.1, "ticket": 555, "status": "DONE", "account_id": "5039911",
        "timestamp": now})
    await bus.publish(EVENT_CMD_FAILED, {
        "request_id": "e5f6", "action": "OPEN", "symbol": "NQ100", "side": "SELL",
        "status": "EXPIRED", "reason": "EXPIRED", "timestamp": now})
    text = _today_file(tmp).read_text(encoding="utf-8")
    assert "ORDER_REJECTED: BUY NQ100" in text and "reason: KILL_SWITCH_ACTIVE" in text, text
    assert "ORDER_RESULT: EXECUTED_ON_PLATFORM (DONE)" in text and "ticket 555" in text, text
    assert "ORDER_RESULT: FAILED (EXPIRED)" in text and "reason: EXPIRED" in text, text
    print("OK — رفض الأمر ونتيجتاه (نجاح/فشل) ثلاثة أسطر عربية واضحة")


async def test_position_appeared_vanished():
    print("\n--- test_position_appeared_vanished ---")
    tmp = tempfile.mkdtemp()
    bus, _ = await _run(tmp)
    now = time.time()
    await bus.publish(EVENT_APPEARED, {
        "ticket": 9001, "symbol": "XAUUSD", "side": "BUY", "volume": 0.05,
        "entry_price": 3355.25, "account_id": "5039911", "timestamp": now})
    await bus.publish(EVENT_VANISHED, {
        "ticket": 9001, "symbol": "XAUUSD", "side": "BUY", "volume": 0.05,
        "profit": -1.2, "account_id": "5039911", "timestamp": now})
    text = _today_file(tmp).read_text(encoding="utf-8")
    assert "POSITION_APPEARED_ON_PLATFORM: BUY XAUUSD" in text and "@ 3355.25" in text, text
    assert "POSITION_VANISHED_FROM_PLATFORM: BUY XAUUSD" in text and "last floating profit -1.2" in text, text
    print("OK — ظهور واختفاء المركز على المنصّة سطران بالعربي")


async def test_health_states():
    print("\n--- test_health_states ---")
    tmp = tempfile.mkdtemp()
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(_cfg(tmp)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY and "READY" in h.message and "lines=0" in h.message, h
    await bus.publish(EVENT_TRADE, {
        "event_type": "OPENED", "ticket": 1, "symbol": "NQ100", "side": "BUY",
        "volume": 1, "entry_price": 1, "source_row_id": 9, "timestamp": time.time()})
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY and h2.details["lines_today"] == 1, h2
    snap = await atom.snapshot()
    assert snap["total_lines"] == 1 and snap["last_lines"], snap
    fresh = Atom()
    await fresh.initialize(bus.make_context(_cfg(tmp)))
    await fresh.restore(snap)
    assert fresh._total_lines == 1 and 9 in fresh._seen_rows, "restore ناقص"
    print("OK — الصحّة: UNHEALTHY → HEALTHY(جاهز lines=0) → today=1 · لقطة/استرجاع سليمان")


async def main():
    tests = [test_opened_line_and_state_event, test_closed_then_cost_revision,
             test_rejected_ack_failed, test_position_appeared_vanished,
             test_health_states]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e))); print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e))); print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}"); sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
