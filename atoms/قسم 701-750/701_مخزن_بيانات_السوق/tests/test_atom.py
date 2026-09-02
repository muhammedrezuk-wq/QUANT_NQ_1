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
    "_atom701", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom701"] = _mod
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
        return AtomContext(atom_id=701, config=cfg, logger=_NullLogger(),
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
            "SELECT symbol, provider, bid, ask, occurred_at FROM market_data"
            " ORDER BY id").fetchall()
    finally:
        conn.close()


async def _make(bus, db_path, flush_size=1, retention_days=30):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": db_path, "flush_size": flush_size,
         "retention_days": retention_days}))
    await atom.start()
    return atom


async def test_persists_price_to_disk():
    print("\n--- test_persists_price_to_disk ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1)
        await bus.publish(EVENT_IN, {"symbol": "NQ100", "bid": 100.0, "ask": 100.5,
                                     "timestamp": 7.0, "provider": "MT5"})
        rows = _rows(db)
        assert len(rows) == 1, rows
        assert rows[0] == ("NQ100", "MT5", 100.0, 100.5, 7.0), rows[0]
        out = [p for n, p in bus.published if n == EVENT_OUT]
        assert out and out[-1]["rows"] == 1 and out[-1]["total"] == 1
        print(f"OK — كتب على القرص فعلاً: {rows[0]} · receipt={out[-1]}")
    finally:
        os.unlink(db)


async def test_buffers_until_flush_size():
    print("\n--- test_buffers_until_flush_size ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=3)
        for i in range(2):
            await bus.publish(EVENT_IN, {"symbol": "NQ100", "bid": i, "ask": i})
        assert _rows(db) == [], "should still be buffered"
        await bus.publish(EVENT_IN, {"symbol": "NQ100", "bid": 2, "ask": 2})
        assert len(_rows(db)) == 3, "flush at size"
        print("OK — يخزّن بالدفعة (buffer) وينزل عند حجم الدفعة")
    finally:
        os.unlink(db)


async def test_ignores_without_symbol():
    print("\n--- test_ignores_without_symbol ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1)
        await bus.publish(EVENT_IN, {"bid": 1, "ask": 2})
        assert _rows(db) == []
        assert not [p for n, p in bus.published if n == EVENT_OUT]
        print("OK — سعر بلا symbol اتجاهل")
    finally:
        os.unlink(db)


async def test_prune_on_day():
    print("\n--- test_prune_on_day ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1, retention_days=1)
        await bus.publish(EVENT_IN, {"symbol": "NQ100", "bid": 1, "ask": 2,
                                     "timestamp": 1000.0})
        await bus.publish(EVENT_IN, {"symbol": "NQ100", "bid": 3, "ask": 4,
                                     "timestamp": 1_000_000.0})
        assert len(_rows(db)) == 2
        await bus.publish(EVENT_DAY, {"official_time": 1_000_000.0})
        remaining = _rows(db)
        assert len(remaining) == 1 and remaining[0][4] == 1_000_000.0, remaining
        pruned = [p for n, p in bus.published if n == EVENT_OUT][-1]["pruned"]
        assert pruned == 1, pruned
        print(f"OK — احتفاظ (retention): حذف القديم، أبقى الحديث · pruned={pruned}")
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
        h = await atom.health_check()
        assert h.state == HealthState.UNHEALTHY, "not started -> UNHEALTHY"
        await atom.start()
        h = await atom.health_check()
        assert h.state == HealthState.DEGRADED, "started, nothing stored -> DEGRADED"
        await bus.publish(EVENT_IN, {"symbol": "NQ100", "bid": 1, "ask": 2})
        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY, "after store -> HEALTHY"
        print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")
    finally:
        os.unlink(db)


async def main():
    tests = [test_persists_price_to_disk, test_buffers_until_flush_size,
             test_ignores_without_symbol, test_prune_on_day, test_health_states]
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
