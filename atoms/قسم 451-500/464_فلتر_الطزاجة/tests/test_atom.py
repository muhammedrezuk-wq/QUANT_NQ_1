import asyncio
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

os.environ.pop("NQ_BRIDGE_DB", None)  # hermetic — لا يتجاوز مسار الاختبار للجسر الحيّ

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom464", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom464"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
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

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, cfg):
        return AtomContext(atom_id=464, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _db(prices):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE prices (symbol TEXT, updated_at REAL)")
    conn.executemany("INSERT INTO prices (symbol, updated_at) VALUES (?, ?)", prices)
    conn.commit()
    conn.close()
    return path


def _cfg(path, threshold=180.0):
    return {"db_path": path, "table_name": "prices",
            "poll_interval_s": 5.0, "stale_threshold_s": threshold}


async def _new(path, threshold=180.0):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(_cfg(path, threshold)))
    atom._running = True
    return atom, bus


def _verdict(bus, symbol):
    hits = [p for n, p in bus.published if n == EVENT_OUT and p["symbol"] == symbol]
    return hits[-1]["metadata"]["passed"] if hits else None


async def test_stale_blocked_fresh_pass():
    print("\n--- test_stale_blocked_fresh_pass ---")
    # newest=1000; STOCK at 500 (age 500 > 180) → block; LIVE at 990 (age 10) → pass
    path = _db([("LIVE", 990.0), ("STOCK", 500.0), ("NEWEST", 1000.0)])
    try:
        atom, bus = await _new(path)
        await atom._check_once()
        assert _verdict(bus, "STOCK") is False, "STOCK يجب أن يُحجب"
        assert _verdict(bus, "LIVE") is True, "LIVE يجب أن يمرّ"
        print("OK — البايت يُحجب، الحيّ يمرّ")
    finally:
        os.unlink(path)


async def test_all_fresh_all_pass():
    print("\n--- test_all_fresh_all_pass ---")
    path = _db([("A", 1000.0), ("B", 1000.0), ("C", 999.0)])
    try:
        atom, bus = await _new(path)
        await atom._check_once()
        for s in ("A", "B", "C"):
            assert _verdict(bus, s) is True, s
        print("OK — كلها حيّة → كلها تمرّ")
    finally:
        os.unlink(path)


async def test_emits_on_every_evaluable_read():
    """حكم المالك 2026-08-16: الحكم ينتهي بانتهاء القراءة التي أنتجته.

    كان النشر عند انقلاب الحكم فقط — و`454` يخزّن حكم الرمز **بلا إطار زمنيّ**،
    فبقي `False` واحد حاكمًا للأبد. المقيس حيًّا وقتها: `reads=281 emitted=8`
    بينما `454` مرّر **صفرًا من ٣٨٦**. فصار النشر على كل قراءة صالحة.
    """
    print("\n--- test_emits_on_every_evaluable_read ---")
    path = _db([("X", 1000.0), ("Y", 400.0)])
    try:
        atom, bus = await _new(path)
        await atom._check_once()          # Y block · X pass
        n1 = len(bus.published)
        assert n1 == 2, n1
        await atom._check_once()          # نفس القراءة → يُعاد الحكم لا يُكتم
        assert len(bus.published) == n1 * 2, "كل قراءة صالحة تُجدّد الحكم"
        # الآن Y صار حيًّا — الحكم يتبع القراءة الجديدة
        conn = sqlite3.connect(path)
        conn.execute("UPDATE prices SET updated_at=1000.0 WHERE symbol='Y'")
        conn.commit(); conn.close()
        await atom._check_once()
        assert _verdict(bus, "Y") is True
        assert len(bus.published) == n1 * 3
        print("OK — حكم لكل قراءة، فلا يبقى حظر قديم حاكمًا")
    finally:
        os.unlink(path)


async def test_live_tick_refreshes_symbol_without_mt5_table():
    print("\n--- test_live_tick_refreshes_symbol_without_mt5_table ---")
    path = _db([])
    try:
        atom, bus = await _new(path, threshold=5.0)
        atom._official_time = 100.0
        await atom._on_tick({"account_id": "A", "broker": "B", "symbol": "NQ",
                             "exchange_timestamp": 98.0})
        assert _verdict(bus, "NQ") is True
        row = [p for n, p in bus.published if n == EVENT_OUT][-1]
        assert row["account_id"] == "A" and row["metadata"]["age_s"] == 2.0
        print("OK — التِكّة الحيّة تجدّد حكم الطزاجة فورًا")
    finally:
        os.unlink(path)


async def test_health_states():
    print("\n--- test_health_states ---")
    path = _db([("A", 1000.0)])
    try:
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(_cfg(path)))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        atom._running = True
        assert (await atom.health_check()).state == HealthState.DEGRADED
        await atom._check_once()
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")
    finally:
        os.unlink(path)


async def main():
    tests = [test_stale_blocked_fresh_pass, test_all_fresh_all_pass,
             test_emits_on_every_evaluable_read,
             test_live_tick_refreshes_symbol_without_mt5_table,
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
