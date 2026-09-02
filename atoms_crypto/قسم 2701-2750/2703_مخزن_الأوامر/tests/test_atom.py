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
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom703", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom703"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_IN = _mod.EVENT_IN
EVENT_DAY = _mod.EVENT_DAY
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
        return AtomContext(atom_id=703, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT account_id, symbol, side, volume, occurred_at FROM orders"
            " ORDER BY id").fetchall()
    finally:
        conn.close()


async def _make(bus, db_path, flush_size=1, retention_days=180):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": db_path, "flush_size": flush_size,
         "retention_days": retention_days}))
    await atom.start()
    return atom


async def test_persists_order_with_account():
    print("\n--- test_persists_order_with_account ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1)
        await bus.publish(EVENT_IN, {"account_id": "A1", "request_id": "r1", "symbol": "NQ100",
                                     "side": "BUY", "volume": 2.0, "timestamp": 5.0})
        rows = _rows(db)
        assert len(rows) == 1, rows
        assert rows[0] == ("A1", "NQ100", "BUY", 2.0, 5.0), rows[0]
        print(f"OK — خزّن الأمر مع account_id (قاعدة ٢٢): {rows[0]}")
    finally:
        os.unlink(db)


async def test_account_isolation_query():
    print("\n--- test_account_isolation_query ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1)
        await bus.publish(EVENT_IN, {"account_id": "A1", "request_id": "rA", "symbol": "NQ100", "side": "BUY"})
        await bus.publish(EVENT_IN, {"account_id": "A2", "request_id": "rB", "symbol": "NQ100", "side": "SELL"})
        conn = sqlite3.connect(db)
        try:
            a1 = conn.execute("SELECT side FROM orders WHERE account_id='A1'").fetchall()
            a2 = conn.execute("SELECT side FROM orders WHERE account_id='A2'").fetchall()
        finally:
            conn.close()
        assert a1 == [("BUY",)] and a2 == [("SELL",)], (a1, a2)
        print("OK — عزل الحسابات: كل حساب صفوفه لحاله (فلترة account_id)")
    finally:
        os.unlink(db)


async def test_ignores_empty():
    print("\n--- test_ignores_empty ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1)
        await bus.publish(EVENT_IN, {"side": "BUY"})
        assert _rows(db) == []
        print("OK — أمر بلا account_id ولا symbol اتجاهل")
    finally:
        os.unlink(db)


async def test_prune_on_day():
    print("\n--- test_prune_on_day ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1, retention_days=1)
        await bus.publish(EVENT_IN, {"account_id": "A1", "request_id": "old", "symbol": "NQ100",
                                     "timestamp": 1000.0})
        await bus.publish(EVENT_IN, {"account_id": "A1", "request_id": "new", "symbol": "NQ100",
                                     "timestamp": 1_000_000.0})
        await bus.publish(EVENT_DAY, {"official_time": 1_000_000.0})
        remaining = _rows(db)
        assert len(remaining) == 1 and remaining[0][4] == 1_000_000.0, remaining
        print("OK — احتفاظ: حذف الأمر القديم، أبقى الحديث")
    finally:
        os.unlink(db)


async def test_health_states():
    print("\n--- test_health_states ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(
            {"db_path": db, "flush_size": 1, "retention_days": 0}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        # جاهز بلا أي أمر بعد = HEALTHY برسالة جاهزية صادقة (أمر المالك: لا تعثّر كاذب)
        ready = await atom.health_check()
        assert ready.state == HealthState.HEALTHY, ready
        assert "READY_AWAITING_FIRST_ORDER_STORE" in ready.message, ready.message
        assert ready.details["stored"] == 0, ready.details
        await bus.publish(EVENT_IN, {"account_id": "A1", "request_id": "health", "symbol": "NQ100"})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> HEALTHY (جاهز، صفر مخزَّن) -> HEALTHY (مخزَّن)")
    finally:
        os.unlink(db)


async def main():
    tests = [test_persists_order_with_account, test_account_isolation_query,
             test_ignores_empty, test_prune_on_day, test_health_states]
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
