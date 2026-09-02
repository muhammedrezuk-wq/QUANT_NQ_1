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
    "_atom716", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom716"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_IN = _mod.EVENT_IN
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
        return AtomContext(atom_id=716, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _make_md(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE market_data (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " symbol TEXT, occurred_at REAL, payload_json TEXT)")
        # 3 rows: two identical (dup), one distinct
        conn.executemany(
            "INSERT INTO market_data (symbol, occurred_at, payload_json) VALUES (?,?,?)",
            [("NQ", 1.0, "{}"), ("NQ", 1.0, "{}"), ("NQ", 2.0, "{}")])
        conn.commit()
    finally:
        conn.close()


def _count(path, table="market_data"):
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


async def test_removes_duplicates_keeps_one():
    print("\n--- test_removes_duplicates_keeps_one ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "market_data.db")
    try:
        _make_md(src)
        assert _count(src) == 3
        bus, _ = await _run({
            "stores": [{"db_path": src, "table": "market_data",
                        "dedup_columns": ["symbol", "occurred_at", "payload_json"]}],
            "vacuum_after_cleanup": False})
        await bus.publish(EVENT_IN, {"rows": 1, "timestamp": 5.0})
        assert _count(src) == 2, "one duplicate removed, distinct kept"
        rep = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert rep["removed"] == 1 and rep["timestamp"] == 5.0, rep
        print(f"OK — حذف صفًّا مكرّرًا وأبقى نسخة + المميّز: removed={rep['removed']}")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_guards_bad_identifier():
    print("\n--- test_guards_bad_identifier ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "market_data.db")
    try:
        _make_md(src)
        bus, atom = await _run({
            "stores": [{"db_path": src, "table": "market_data",
                        "dedup_columns": ["symbol; DROP TABLE market_data"]}],
            "vacuum_after_cleanup": False})
        await bus.publish(EVENT_IN, {"rows": 1})
        assert _count(src) == 3, "malicious column rejected, nothing deleted"
        h = await atom.health_check()
        assert "bad column" in (h.message or ""), h.message
        print("OK — حرس المعرّفات: عمود خبيث اترفض (الجدول سليم)")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_health_states():
    print("\n--- test_health_states ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "market_data.db")
    try:
        _make_md(src)
        cfg = {"stores": [{"db_path": src, "table": "market_data",
                           "dedup_columns": ["symbol", "occurred_at", "payload_json"]}],
               "vacuum_after_cleanup": False}
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(cfg))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY, (h.state, h.message)
        assert "READY" in (h.message or "") and "runs=0" in (h.message or ""), h.message
        await bus.publish(EVENT_IN, {"rows": 1})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> HEALTHY(جاهز بانتظار أول عمل) -> HEALTHY")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_health_shows_pending_resume():
    print("\n--- test_health_shows_pending_resume ---")
    d = tempfile.mkdtemp()
    src1 = os.path.join(d, "market_data.db")
    src2 = os.path.join(d, "analysis.db")
    try:
        _make_md(src1)
        cfg = {"stores": [
                   {"db_path": src1, "table": "market_data",
                    "dedup_columns": ["symbol", "occurred_at", "payload_json"]},
                   {"db_path": src2, "table": "analysis",
                    "dedup_columns": ["symbol", "occurred_at", "payload_json"]}],
               "vacuum_after_cleanup": False}
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(cfg))
        await atom.start()
        # لقطة مستعادة: جولة سابقة توقّفت بعد المخزن الأوّل — الاستئناف من الثاني
        await atom.restore({"next_store_index": 1, "last_success": None,
                            "runs": 0, "removed_total": 0, "reclaimed_bytes": 0})
        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY, (h.state, h.message)
        assert "incomplete" in (h.message or ""), h.message
        assert "analysis" in (h.message or ""), h.message
        assert h.details["next_store_index"] == 1, h.details
        print(f"OK — الاستئناف المعلّق ظاهر بصدق: {h.message}")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def main():
    tests = [test_removes_duplicates_keeps_one, test_guards_bad_identifier,
             test_health_states, test_health_shows_pending_resume]
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
