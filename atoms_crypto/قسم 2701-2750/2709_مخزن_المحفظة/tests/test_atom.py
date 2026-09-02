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
    "_atom709", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom709"] = _mod
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
        return AtomContext(atom_id=709, config=cfg, logger=_NullLogger(),
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
            "SELECT account_id, equity, balance, open_count, occurred_at"
            " FROM portfolio ORDER BY id").fetchall()
    finally:
        conn.close()


async def _make(bus, db_path, min_interval=60.0, retention_days=365):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": db_path, "min_write_interval_s": min_interval,
         "retention_days": retention_days}))
    await atom.start()
    return atom


async def test_persists_summary_with_account():
    print("\n--- test_persists_summary_with_account ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, min_interval=0.0)
        await bus.publish(EVENT_IN, {"account_id": "A1", "equity": 1000.0,
                                     "balance": 950.0, "open_count": 2, "timestamp": 5.0})
        rows = _rows(db)
        assert rows == [("A1", 1000.0, 950.0, 2, 5.0)], rows
        print(f"OK — خزّن المحفظة مع account_id (قاعدة ٢٢): {rows[0]}")
    finally:
        os.unlink(db)


async def test_per_account_throttle():
    print("\n--- test_per_account_throttle ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        atom = await _make(bus, db, min_interval=60.0)
        # A1 at t=100 (write), A1 at t=110 (throttled: <60s), A2 at t=110 (writes: own clock)
        await bus.publish(EVENT_IN, {"account_id": "A1", "equity": 1, "timestamp": 100.0})
        await bus.publish(EVENT_IN, {"account_id": "A1", "equity": 2, "timestamp": 110.0})
        await bus.publish(EVENT_IN, {"account_id": "A2", "equity": 9, "timestamp": 110.0})
        rows = _rows(db)
        accts = sorted(r[0] for r in rows)
        assert accts == ["A1", "A2"], (rows, "A2 must NOT be throttled by A1's clock")
        assert atom.skipped_count == 1, atom.skipped_count
        print("OK — الكبح لكل حساب: A1 المكرّر انكبح · A2 كتب (ما تأثّر بساعة A1)")
    finally:
        os.unlink(db)


async def test_prune_on_day():
    print("\n--- test_prune_on_day ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db, min_interval=0.0, retention_days=1)
        await bus.publish(EVENT_IN, {"account_id": "A1", "equity": 1, "timestamp": 1000.0})
        await bus.publish(EVENT_IN, {"account_id": "A1", "equity": 2,
                                     "timestamp": 1_000_000.0})
        await bus.publish(EVENT_DAY, {"official_time": 1_000_000.0})
        remaining = _rows(db)
        assert len(remaining) == 1 and remaining[0][4] == 1_000_000.0, remaining
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
            {"db_path": db, "min_write_interval_s": 0.0, "retention_days": 0}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        assert (await atom.health_check()).state == HealthState.DEGRADED
        await bus.publish(EVENT_IN, {"account_id": "A1", "equity": 1})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")
    finally:
        os.unlink(db)


async def main():
    tests = [test_persists_summary_with_account, test_per_account_throttle,
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
