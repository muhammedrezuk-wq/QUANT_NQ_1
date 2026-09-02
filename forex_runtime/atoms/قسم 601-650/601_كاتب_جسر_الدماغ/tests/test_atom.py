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

os.environ.pop("NQ_BRIDGE_DB", None)  # اختبار محكم: لا يتأثّر بمتغيّر جسر الإنتاج NQ_BRIDGE_DB

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402
from pathlib import Path as _AtomPath  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom601", _AtomPath(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom601"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_WRITTEN = _mod.EVENT_WRITTEN
EVENT_WRITE_FAILED = _mod.EVENT_WRITE_FAILED
EVENT_HALTED = _mod.EVENT_HALTED

MY_ACCOUNT = "474099934"


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
        cfg = {"account_id": MY_ACCOUNT, "db_path": db_path, "heartbeat_interval_s": 1.0, "magic": 20260801, "cursor_db": db_path + ".cursor"}
        return AtomContext(atom_id=601, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _pending(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT count(*) FROM commands WHERE status='PENDING'").fetchone()[0]
    finally:
        conn.close()


def _decision(**over):
    base = {"account_id": MY_ACCOUNT, "magic": 20260801, "request_id": "r1", "symbol": "NQ",
            "side": "BUY", "volume": 0.1, "reference_price": 100.0,
            "stop_loss": 99.0, "take_profit": 102.0}
    base.update(over)
    return base


async def test_writes_command_for_my_account():
    print("\n--- test_writes_command_for_my_account ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "nq_brain.db")
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE IF NOT EXISTS account_v2(account_id TEXT PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO account_v2 VALUES (?)", (MY_ACCOUNT,))
        connection.commit(); connection.close()
        await atom.start()
        await atom._on_final_decision(_decision())
        assert _pending(db) == 1, "لازم يكتب أمر PENDING واحد"
        written = [p for n, p in bus.published if n == EVENT_WRITTEN]
        assert written and written[0]["account_id"] == MY_ACCOUNT
        print("OK — كتب أمر PENDING لحسابه + نشر written")


async def test_ignores_decision_for_other_account():
    """🔴 عزل الحساب (قاعدة 22) — قرار لحساب آخر لا يُكتب."""
    print("\n--- test_ignores_decision_for_other_account ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "nq_brain.db")
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE IF NOT EXISTS account_v2(account_id TEXT PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO account_v2 VALUES (?)", (MY_ACCOUNT,))
        connection.commit(); connection.close()
        await atom.start()
        await atom._on_final_decision(_decision(account_id="999_OTHER"))
        assert _pending(db) == 0, "قرار حساب آخر ما لازم يُكتب"
        assert not [p for n, p in bus.published if n == EVENT_WRITTEN]
        print("OK — تجاهل قرار حساب آخر (عزل)")


async def test_ignores_decision_without_account_id():
    """🔴 Fail Closed — قرار بلا account_id لا يُكتب."""
    print("\n--- test_ignores_decision_without_account_id ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "nq_brain.db")
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE IF NOT EXISTS account_v2(account_id TEXT PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO account_v2 VALUES (?)", (MY_ACCOUNT,))
        connection.commit(); connection.close()
        await atom.start()
        d = _decision(); d.pop("account_id")
        await atom._on_final_decision(d)
        assert _pending(db) == 0, "قرار بلا هوية ما يُكتب"
        print("OK — Fail Closed: قرار بلا account_id تجاهله")


async def test_rejects_missing_volume():
    print("\n--- test_rejects_missing_volume ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "nq_brain.db")
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE IF NOT EXISTS account_v2(account_id TEXT PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO account_v2 VALUES (?)", (MY_ACCOUNT,))
        connection.commit(); connection.close()
        await atom.start()
        await atom._on_final_decision(_decision(volume=None))
        assert _pending(db) == 0
        assert [p for n, p in bus.published if n == EVENT_WRITE_FAILED]
        print("OK — رفض أمر بلا حجم (write_failed)")


async def test_halt_cancels_pending():
    print("\n--- test_halt_cancels_pending ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "nq_brain.db")
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE IF NOT EXISTS account_v2(account_id TEXT PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO account_v2 VALUES (?)", (MY_ACCOUNT,))
        connection.commit(); connection.close()
        await atom.start()
        await atom._on_final_decision(_decision())
        assert _pending(db) == 1
        await atom._on_emergency_halt({"reason": "test"})
        assert _pending(db) == 0, "التوقف الطارئ يلغي المعلّق"
        assert [p for n, p in bus.published if n == EVENT_HALTED]
        print("OK — emergency.halt ألغى الأوامر المعلّقة")


async def test_identity_thread_rides_bridge_row_and_results():
    """بند 22 حزمة ت (ت١): معرف القرار والدورة يُكتبان داخل params_json
    (عمود داخلي قائم — بنية الجدول المتفقة مع الإكسبرت لم تُمسّ)، وأحداث
    النتيجة تسترجعهما من الصف نفسه — صامد على أي إقلاع."""
    print("\n--- test_identity_thread_rides_bridge_row_and_results ---")
    import json as _json
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "nq_brain.db")
        # القرص الدائم للمؤشّر (_CURSOR_DB) مسار عالمي ثابت بالإنتاج عمداً --
        # لكن هذا يعني أن تشغيلتين متتاليتين لهذا الاختبار كانتا تتشاركان
        # نفس الملف الحقيقي على القرص وتتسمّمان من بعضهما (id=1 دائماً بقاعدة
        # مؤقّتة جديدة، فمؤشّر الحياة الماضية كان يصادف قيم هذا التشغيل بالضبط
        # فيرفض الصفّ). يُعزَل هنا فقط -- مسار الإنتاج بـatom.py لم يُمسّ.
        _mod._CURSOR_DB = os.path.join(tmp, "bridge_cursor_601.db")
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE IF NOT EXISTS account_v2(account_id TEXT PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO account_v2 VALUES (?)", (MY_ACCOUNT,))
        connection.commit(); connection.close()
        await atom.start()
        await atom._on_pulse({})  # تثبيت مؤشر النتائج قبل كتابة الأمر
        await atom._on_final_decision(_decision(decision_id="D-1", gate_request_id="G-1"))
        connection = sqlite3.connect(db)
        row = connection.execute("SELECT params_json FROM commands WHERE request_id='r1'").fetchone()
        metadata = _json.loads(row[0])
        assert metadata["decision_id"] == "D-1" and metadata["gate_request_id"] == "G-1", metadata
        # محاكاة الإكسبرت (الحد الأدنى): إتمام الصف نفسه دون أي حقل خارجي جديد
        connection.execute("UPDATE commands SET status='DONE', result='ok', done_at=?, ticket=42 WHERE request_id='r1'", (1000.0,))
        connection.commit(); connection.close()
        await atom._on_pulse({})
        acks = [p for n, p in bus.published if n == "execution.command.ack"]
        assert acks and acks[-1]["decision_id"] == "D-1" and acks[-1]["gate_request_id"] == "G-1", acks
        assert atom.identity_incomplete == 0
        # وأمر بلا هوية إطلاقًا يُعَدّ (إنذار identity_incomplete) ولا يُحجب هنا
        await atom._on_final_decision(_decision(request_id="r2"))
        assert atom.identity_incomplete == 1
        print("OK — ت١: الهوية بالصف وبأحداث النتيجة، والغائب يُعَدّ لا يُحجب")


async def main():
    tests = [
        test_writes_command_for_my_account,
        test_ignores_decision_for_other_account,
        test_ignores_decision_without_account_id,
        test_rejects_missing_volume,
        test_halt_cancels_pending,
        test_identity_thread_rides_bridge_row_and_results,
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
