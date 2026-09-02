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
    "_atom713", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom713"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_IN = _mod.EVENT_IN
EVENT_DAY = _mod.EVENT_DAY
EVENT_OUT = _mod.EVENT_OUT
_TABLE = "structure"


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
        return AtomContext(atom_id=713, config=cfg, logger=_NullLogger(),
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
            "SELECT symbol, occurred_at FROM %s ORDER BY id" % _TABLE).fetchall()
    finally:
        conn.close()


async def _make(bus, db_path, flush_size=1, retention_days=90):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": db_path, "flush_size": flush_size,
         "retention_days": retention_days}))
    await atom.start()
    return atom


async def test_persists_symbol_fact():
    print("\n--- test_persists_symbol_fact ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1)
        await bus.publish(EVENT_IN, {"symbol": "NQ100", "hh": 1, "timestamp": 8.0})
        rows = _rows(db)
        assert rows == [("NQ100", 8.0)], rows
        print(f"OK — خزّن البنية (رمز + وقت + payload كامل): {rows[0]}")
    finally:
        os.unlink(db)


async def test_ignores_without_symbol():
    print("\n--- test_ignores_without_symbol ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1)
        await bus.publish(EVENT_IN, {"hh": 1})
        assert _rows(db) == []
        print("OK — حدث بلا symbol اتجاهل")
    finally:
        os.unlink(db)


async def test_prune_on_day():
    print("\n--- test_prune_on_day ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, flush_size=1, retention_days=1)
        await bus.publish(EVENT_IN, {"symbol": "NQ100", "timestamp": 1000.0})
        await bus.publish(EVENT_IN, {"symbol": "NQ100", "timestamp": 1_000_000.0})
        await bus.publish(EVENT_DAY, {"official_time": 1_000_000.0})
        remaining = _rows(db)
        assert len(remaining) == 1 and remaining[0][1] == 1_000_000.0, remaining
        print("OK — احتفاظ: حذف القديم أبقى الحديث")
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
        assert (await atom.health_check()).state == HealthState.DEGRADED
        await bus.publish(EVENT_IN, {"symbol": "NQ100"})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")
    finally:
        os.unlink(db)


async def main():
    tests = [test_persists_symbol_fact, test_ignores_without_symbol,
             test_prune_on_day, test_health_states]
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
