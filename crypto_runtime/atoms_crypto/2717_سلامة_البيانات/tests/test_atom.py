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
    "_atom717", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom717"] = _mod
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
        return AtomContext(atom_id=717, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _make_db(path, table, time_col, rows):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE %s (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " symbol TEXT, %s REAL)" % (table, time_col))
        conn.executemany("INSERT INTO %s (symbol, %s) VALUES (?,?)" % (table, time_col),
                         rows)
        conn.commit()
    finally:
        conn.close()


async def _run(cfg):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg))
    await atom.start()
    return bus, atom


async def test_sound_when_clean():
    print("\n--- test_sound_when_clean ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "market_data.db")
    try:
        _make_db(src, "market_data", "occurred_at", [("NQ", 10.0), ("NQ", 20.0)])
        bus, _ = await _run({
            "stores": [{"db_path": src, "table": "market_data",
                        "time_column": "occurred_at"}],
            "warn_on_empty_table": False})
        await bus.publish(EVENT_IN, {"timestamp": 1000.0})
        out = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert out["verdict"] == "SOUND" and out["flags"] == [], out
        print(f"OK — بيانات سليمة: verdict=SOUND · rows={out['rows_total']}")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_flags_future_and_missing_stamp():
    print("\n--- test_flags_future_and_missing_stamp ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "market_data.db")
    try:
        # one row in the future (9M > now 1000), one with NULL stamp
        _make_db(src, "market_data", "occurred_at", [("NQ", 9_000_000.0), ("NQ", None)])
        bus, atom = await _run({
            "stores": [{"db_path": src, "table": "market_data",
                        "time_column": "occurred_at"}],
            "warn_on_empty_table": False})
        await bus.publish(EVENT_IN, {"timestamp": 1000.0})
        out = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert out["verdict"] == "SUSPECT", out
        assert "ROWS_IN_THE_FUTURE" in out["flags"] and "ROWS_WITHOUT_STAMP" in out["flags"]
        h = await atom.health_check()
        assert h.state == HealthState.DEGRADED
        print(f"OK — كشف صفوف مستقبلية وبلا ختم: flags={out['flags']}")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_respects_time_column():
    print("\n--- test_respects_time_column ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "trades.db")
    try:
        _make_db(src, "trades", "closed_at", [("NQ", 9_000_000.0)])
        bus, _ = await _run({
            "stores": [{"db_path": src, "table": "trades",
                        "time_column": "closed_at"}],
            "warn_on_empty_table": False})
        await bus.publish(EVENT_IN, {"timestamp": 1000.0})
        out = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert "ROWS_IN_THE_FUTURE" in out["flags"], out
        print("OK — فحص السلامة عبر time_column الصحيح (closed_at للصفقات)")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def test_health_states():
    print("\n--- test_health_states ---")
    d = tempfile.mkdtemp()
    src = os.path.join(d, "market_data.db")
    try:
        _make_db(src, "market_data", "occurred_at", [("NQ", 10.0)])
        cfg = {"stores": [{"db_path": src, "table": "market_data",
                           "time_column": "occurred_at"}],
               "warn_on_empty_table": False}
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(cfg))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        h = await atom.health_check()
        assert h.state == HealthState.HEALTHY, (h.state, h.message)
        assert "READY" in (h.message or "") and "runs=0" in (h.message or ""), h.message
        await bus.publish(EVENT_IN, {"timestamp": 1000.0})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> HEALTHY(جاهز بانتظار أول عمل) -> HEALTHY")
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


async def main():
    tests = [test_sound_when_clean, test_flags_future_and_missing_stamp,
             test_respects_time_column, test_health_states]
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
