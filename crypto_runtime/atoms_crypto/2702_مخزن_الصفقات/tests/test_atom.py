import asyncio
import inspect
import os
import sqlite3
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom702", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom702"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_TRADE = _mod.EVENT_TRADE
EVENT_OUTCOME = _mod.EVENT_OUTCOME
EVENT_OUT = _mod.EVENT_OUT


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
        return AtomContext(atom_id=702, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _trade(event_type, **kw):
    d = {"event_type": event_type}
    d.update(kw)
    return d


async def _make(bus, db_path):
    atom = Atom()
    await atom.initialize(bus.make_context({"db_path": db_path}))
    await atom.start()
    return atom


async def test_stores_opened_from_platform_event():
    print("\n--- test_stores_opened_from_platform_event ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db)
        await bus.publish(EVENT_TRADE, _trade(
            "OPENED", account_id="A1", ticket=55, symbol="NQ100", side="BUY",
            volume=1.0, open_time=3.0, timestamp=3.0))
        rows = _query(db, "SELECT kind, account_id, ticket, symbol, size, opened_at FROM trades")
        assert rows == [("OPENED", "A1", 55, "NQ100", 1.0, 3.0)], rows
        out = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert out["account_id"] == "A1" and out["ticket"] == 55, out
        print(f"OK — خزّن من platform.trade_event مع account_id + حقول 611: {rows[0]}")
    finally:
        os.unlink(db)


async def test_dedupe_by_source_row():
    print("\n--- test_dedupe_by_source_row ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        atom = await _make(bus, db)
        ev = _trade("OPENED", account_id="A1", ticket=55, symbol="NQ100", source_row_id=900)
        await bus.publish(EVENT_TRADE, ev)
        await bus.publish(EVENT_TRADE, ev)
        rows = _query(db, "SELECT COUNT(*) FROM trades")
        assert rows[0][0] == 1, rows
        assert atom.duplicate_count == 1, atom.duplicate_count
        print("OK — تكرار نفس الحدث (source_row_id) اتجاهل")
    finally:
        os.unlink(db)


async def test_close_pnl_then_enrich():
    print("\n--- test_close_pnl_then_enrich ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        atom = await _make(bus, db)
        await bus.publish(EVENT_TRADE, _trade(
            "CLOSED", account_id="A1", ticket=55, symbol="NQ100", profit=50.0,
            close_time=9.0, source_row_id=5))
        # pnl مباشرة من ربح 611
        rows = _query(db, "SELECT pnl, closed_at FROM trades WHERE ticket=55")
        assert rows == [(50.0, 9.0)], rows
        # ثم إثراء من 563 market.outcome.realized (نسبة/نتيجة)
        await bus.publish(EVENT_OUTCOME, {"ticket": 55, "pnl": 120.5, "pnl_pct": 1.2,
                                          "result": "WIN", "strategy_id": "breakout"})
        rows = _query(db, "SELECT pnl, pnl_pct, result, strategy_id FROM trades WHERE ticket=55")
        assert rows == [(120.5, 1.2, "WIN", "breakout")], rows
        assert atom.enriched_count == 1
        print(f"OK — ربح من 611 ثم إثراء من النتيجة: {rows[0]}")
    finally:
        os.unlink(db)


async def test_partial_kind():
    print("\n--- test_partial_kind ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db)
        await bus.publish(EVENT_TRADE, _trade("PARTIAL", ticket=7, symbol="NQ100"))
        rows = _query(db, "SELECT kind, partial FROM trades WHERE ticket=7")
        assert rows == [("PARTIAL", 1)], rows
        print("OK — event_type=PARTIAL يتسجّل PARTIAL")
    finally:
        os.unlink(db)


async def test_ignores_unknown_event_type():
    print("\n--- test_ignores_unknown_event_type ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        atom = await _make(bus, db)
        await bus.publish(EVENT_TRADE, _trade("REJECTED", ticket=9, symbol="NQ100"))
        rows = _query(db, "SELECT COUNT(*) FROM trades")
        assert rows[0][0] == 0, rows
        assert atom.stored_count == 0
        print("OK — نوع حدث غير معروف يُتجاهَل (لا يُخزَّن)")
    finally:
        os.unlink(db)


async def test_health_states():
    print("\n--- test_health_states ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context({"db_path": db}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        # جاهز بلا أي صفقة بعد = HEALTHY برسالة جاهزية صادقة (أمر المالك: لا تعثّر كاذب)
        ready = await atom.health_check()
        assert ready.state == HealthState.HEALTHY, ready
        assert "READY_AWAITING_FIRST_TRADE_STORE" in ready.message, ready.message
        assert ready.details["stored"] == 0, ready.details
        await bus.publish(EVENT_TRADE, _trade("OPENED", account_id="A1", symbol="NQ100", ticket=1))
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> HEALTHY (جاهز، صفر مخزَّن) -> HEALTHY (مخزَّن)")
    finally:
        os.unlink(db)


async def main():
    tests = [test_stores_opened_from_platform_event, test_dedupe_by_source_row,
             test_close_pnl_then_enrich, test_partial_kind,
             test_ignores_unknown_event_type, test_health_states]
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
