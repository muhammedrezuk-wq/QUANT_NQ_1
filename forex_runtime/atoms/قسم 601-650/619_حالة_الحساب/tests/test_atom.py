import asyncio
import inspect
import os
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402
from pathlib import Path as _AtomPath  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom619", _AtomPath(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom619"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

# البند ٥٥: `max_age_s` إعداد إلزاميّ بالبطاقة — عتبة التقادم المعلَنة
CFG = {"db_path": "unused.db", "table_name": "account_v2", "poll_interval_s": 1.0,
       "max_age_s": 300.0}

_ROW = {
    "account_id": "ACC-1", "balance": 10000.0, "equity": 10050.0, "margin": 200.0,
    "free_margin": 9850.0, "margin_level": 5025.0, "currency": "USD", "leverage": 500,
    "open_count": 1, "broker": "Exness", "account_server": "Exness-MT5Trial15",
    "connected": 1, "trade_allowed": 1, "expert_allowed": 1, "bridge_beat": 123.0,
    "updated_at": 1000.0,
}


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

    def make_context(self):
        return AtomContext(atom_id=619, config=CFG, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _atom(bus, row_stub):
    atom = Atom()
    await atom.initialize(bus.make_context())
    atom._read_row = row_stub
    return atom


async def test_publishes_account_state_with_account_id():
    print("\n--- test_publishes_account_state_with_account_id ---")
    bus = FakeEventBus()
    atom = await _atom(bus, lambda: dict(_ROW))
    await atom._read_once()
    states = [p for n, p in bus.published if n == EVENT_OUT]
    assert len(states) == 1
    assert states[0]["account_id"] == "ACC-1" and states[0]["balance"] == 10000.0
    assert EVENT_OUT.endswith(".state"), "قابل للإعادة (قاعدة 16)"
    print(f"OK — نشر {EVENT_OUT} للحساب {states[0]['account_id']}")


async def test_refuses_row_without_account_id():
    """🔴 أخطر اختبار — أمان مال حقيقي (قاعدة 22): صفّ بلا account_id يُرفَض."""
    print("\n--- test_refuses_row_without_account_id ---")
    bus = FakeEventBus()
    bad = dict(_ROW); bad["account_id"] = None
    atom = await _atom(bus, lambda: bad)
    await atom._read_once()
    assert not [p for n, p in bus.published if n == EVENT_OUT], "ممنوع ينشر حساب بلا هوية"
    assert atom.no_identity_count == 1
    print("OK — Fail Closed: رفض صفّ بلا account_id (ما خلط حسابات)")


async def test_publish_on_change_only():
    print("\n--- test_publish_on_change_only ---")
    bus = FakeEventBus()
    atom = await _atom(bus, lambda: dict(_ROW))
    await atom._read_once()
    await atom._read_once()  # same updated_at
    states = [p for n, p in bus.published if n == EVENT_OUT]
    assert len(states) == 1, "نفس updated_at → نشر مرّة"
    print("OK — نشر عند التغيّر فقط (مرّة رغم قراءتين)")


async def test_missing_table_degraded_without_crash():
    """حالة فشل (قاعدة 9) — جدول مفقود: DEGRADED بلا انهيار."""
    print("\n--- test_missing_table_degraded_without_crash ---")
    bus = FakeEventBus()

    def boom():
        raise sqlite3.OperationalError("no such table: account")

    atom = await _atom(bus, boom)
    atom._running = True
    await atom._read_once()
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED, "جدول مفقود = DEGRADED لا UNHEALTHY"
    print(f"OK — جدول مفقود: DEGRADED ({h.message}) بلا انهيار")


async def main():
    tests = [
        test_publishes_account_state_with_account_id,
        test_refuses_row_without_account_id,
        test_publish_on_change_only,
        test_missing_table_degraded_without_crash,
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
