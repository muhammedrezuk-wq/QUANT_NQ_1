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

os.environ.pop("NQ_BRIDGE_DB", None)  # اختبار محكم: لا يتأثّر بمتغيّر جسر الإنتاج NQ_BRIDGE_DB

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402
from pathlib import Path as _AtomPath  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom609", _AtomPath(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom609"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_OPENED = _mod.EVENT_OPENED
EVENT_CLOSED = _mod.EVENT_CLOSED


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

    def make_context(self, db_path):
        cfg = {"db_path": db_path, "table_name": "positions_v2", "poll_interval_s": 1.0}
        return AtomContext(atom_id=609, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE positions_v2 (ticket INTEGER, symbol TEXT, side TEXT, volume REAL,"
                 " entry_price REAL, current_price REAL, stop_loss REAL, take_profit REAL,"
                 " profit REAL, swap REAL, magic INTEGER, opened_at REAL, updated_at REAL, account_id TEXT)")
    for r in rows:
        conn.execute("INSERT INTO positions_v2 (ticket,symbol,side,volume,profit,updated_at,account_id)"
                     " VALUES (?,?,?,?,?,?,?)", r)
    conn.commit()
    conn.close()


async def test_publishes_positions_state_with_account_id():
    print("\n--- test_publishes_positions_state_with_account_id ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "b.db")
        _make_db(db, [(1001, "NQ", "BUY", 0.1, 5.0, 100.0, "ACC-1")])
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        await atom._read_once()
        states = [p for n, p in bus.published if n == EVENT_OUT]
        assert states and states[-1]["open_count"] == 1
        pos = states[-1]["positions"][0]
        assert pos["account_id"] == "ACC-1" and pos["ticket"] == 1001
        assert EVENT_OUT.endswith(".state")
        print("OK — نشر المراكز مع account_id لكل مركز")


async def test_detects_appeared_and_vanished():
    print("\n--- test_detects_appeared_and_vanished ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "b.db")
        _make_db(db, [(1, "NQ", "BUY", 0.1, 1.0, 10.0, "ACC-1")])
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        await atom._read_once()  # ticket 1 appears
        assert [p for n, p in bus.published if n == EVENT_OPENED]
        # remove the position -> vanished
        conn = sqlite3.connect(db); conn.execute("DELETE FROM positions_v2"); conn.commit(); conn.close()
        await atom._read_once()
        assert [p for n, p in bus.published if n == EVENT_CLOSED], "لازم يكشف اختفاء المركز"
        print("OK — كشف ظهور واختفاء المركز")


async def test_unreadable_bridge_degraded():
    """حالة فشل (قاعدة 9) — جسر غير موجود: DEGRADED بلا انهيار."""
    print("\n--- test_unreadable_bridge_degraded ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(os.path.join("Z:\\", "no_such.db")))
    atom._running = True
    await atom._read_once()
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED
    print("OK — جسر غير مقروء: DEGRADED بلا انهيار")


async def main():
    tests = [
        test_publishes_positions_state_with_account_id,
        test_detects_appeared_and_vanished,
        test_unreadable_bridge_degraded,
    ]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
