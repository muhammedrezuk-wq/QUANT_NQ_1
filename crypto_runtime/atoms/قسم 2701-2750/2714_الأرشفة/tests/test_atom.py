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
    "_atom714", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom714"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_DAY = _mod.EVENT_DAY
EVENT_OUT = _mod.EVENT_OUT
EVENT_PULSE = _mod.EVENT_PULSE


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
        return AtomContext(atom_id=714, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tmp():
    d = tempfile.mkdtemp()
    return d


def _make_source(path, table, time_col, rows):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE %s (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " symbol TEXT, %s REAL)" % (table, time_col))
        conn.executemany("INSERT INTO %s (symbol, %s) VALUES (?,?)" % (table, time_col),
                         rows)
        conn.commit()
    finally:
        conn.close()


def _count(path, table):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    finally:
        conn.close()


async def _run(cfg):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg))
    await atom.start()
    return bus, atom


async def test_archives_old_rows_and_deletes():
    print("\n--- test_archives_old_rows_and_deletes ---")
    d = _tmp()
    src = os.path.join(d, "market_data.db")
    arch = os.path.join(d, "archive.db")
    try:
        _make_source(src, "market_data", "occurred_at",
                     [("NQ", 100.0), ("NQ", 200.0), ("NQ", 9_000_000.0)])
        bus, _ = await _run({
            "stores": [{"db_path": src, "table": "market_data",
                        "time_column": "occurred_at"}],
            "archive_db_path": arch, "archive_after_days": 1, "batch_limit": 100})
        # cutoff = 1_000_000 - 1day(86400) = 913600 -> rows 100,200 old; 9M recent
        await bus.publish(EVENT_DAY, {"official_time": 1_000_000.0})
        assert _count(src, "market_data") == 1, "recent row stays in source"
        rep = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert rep["archive_path"] != arch and rep["active_archive_path"] == arch
        assert _count(rep["archive_path"], "market_data") == 2, "2 old rows archived"
        assert rep["rows"] == 2, rep
        print(f"OK — نقل الصفوف القديمة للأرشيف وحذفها من المصدر: {rep['per_table']}")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_respects_time_column():
    print("\n--- test_respects_time_column ---")
    d = _tmp()
    src = os.path.join(d, "trades.db")
    arch = os.path.join(d, "archive.db")
    try:
        # trades keyed by closed_at, NOT occurred_at (the old bug)
        _make_source(src, "trades", "closed_at", [("NQ", 10.0), ("NQ", 9_000_000.0)])
        bus, _ = await _run({
            "stores": [{"db_path": src, "table": "trades",
                        "time_column": "closed_at"}],
            "archive_db_path": arch, "archive_after_days": 1, "batch_limit": 100})
        await bus.publish(EVENT_DAY, {"official_time": 1_000_000.0})
        rep = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert _count(rep["archive_path"], "trades") == 1, "old closed trade archived via closed_at"
        assert _count(src, "trades") == 1
        print("OK — احترم time_column (closed_at للصفقات) — الإصلاح شغّال")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_guards_bad_identifier():
    print("\n--- test_guards_bad_identifier ---")
    d = _tmp()
    arch = os.path.join(d, "archive.db")
    try:
        bus, atom = await _run({
            "stores": [{"db_path": os.path.join(d, "x.db"),
                        "table": "bad; DROP TABLE x", "time_column": "occurred_at"}],
            "archive_db_path": arch, "archive_after_days": 1, "batch_limit": 100})
        await bus.publish(EVENT_DAY, {"official_time": 1_000_000.0})
        h = await atom.health_check()
        assert "bad store spec" in (h.message or ""), h.message
        print("OK — حرس المعرّفات: اسم جدول خبيث اترفض (لا حقن SQL)")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_health_states():
    print("\n--- test_health_states ---")
    d = _tmp()
    src = os.path.join(d, "market_data.db")
    arch = os.path.join(d, "archive.db")
    try:
        _make_source(src, "market_data", "occurred_at", [("NQ", 10.0)])
        cfg = {"stores": [{"db_path": src, "table": "market_data",
                           "time_column": "occurred_at"}],
               "archive_db_path": arch, "archive_after_days": 1, "batch_limit": 100}
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(cfg))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        h = await atom.health_check()
        assert h.state == HealthState.DEGRADED, h.message
        assert "AWAITING_FIRST_PULSE" in (h.message or ""), h.message
        await bus.publish(EVENT_DAY, {"official_time": 1_000_000.0})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> DEGRADED(AWAITING_FIRST_PULSE) -> HEALTHY")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_within_window_is_ready_not_degraded():
    print("\n--- test_within_window_is_ready_not_degraded ---")
    d = _tmp()
    src = os.path.join(d, "market_data.db")
    arch = os.path.join(d, "archive.db")
    try:
        _make_source(src, "market_data", "occurred_at", [("NQ", 999_000.0)])
        bus, atom = await _run({
            "stores": [{"db_path": src, "table": "market_data",
                        "time_column": "occurred_at"}],
            "archive_db_path": arch, "archive_after_days": 1, "batch_limit": 100})
        # آخر نجاح قبل ساعة — داخل نافذة 24 ساعة ⇒ اللحاق يقرّر ألّا يعمل
        await atom.restore({"last_success": 1_000_000.0 - 3600.0})
        await bus.publish(EVENT_PULSE, {"official_time": 1_000_000.0})
        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY, (h.state, h.message)
        assert "WITHIN_WINDOW" in (h.message or ""), h.message
        assert "READY" in (h.message or ""), h.message
        assert h.details["runs"] == 0, h.details
        assert not [p for n, p in bus.published if n == EVENT_OUT], \
            "داخل النافذة ⇒ لا أرشفة عند الإقلاع"
        print(f"OK — داخل النافذة: HEALTHY برسالة صادقة: {h.message}")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def main():
    tests = [test_archives_old_rows_and_deletes, test_respects_time_column,
             test_guards_bad_identifier, test_health_states,
             test_within_window_is_ready_not_degraded]
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
