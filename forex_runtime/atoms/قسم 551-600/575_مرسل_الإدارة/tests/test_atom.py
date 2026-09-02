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

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom575", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom575"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom


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

    def make_context(self, config):
        return AtomContext(atom_id=575, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    return path


def _cleanup(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def _read(path):
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(path)
    try:
        try:
            return conn.execute(
                "SELECT action, ticket, stop_loss, volume, status FROM commands"
            ).fetchall()
        except sqlite3.Error:
            return []
    finally:
        conn.close()


async def _new(enabled, db_path):
    os.environ.pop("NQ_BRIDGE_DB", None)
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"enabled": enabled, "db_path": db_path, "magic": 20260801}))
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE IF NOT EXISTS positions_v2(account_id TEXT, ticket INTEGER, symbol TEXT, magic INTEGER)")
    connection.executemany("INSERT INTO positions_v2 VALUES (?,?,?,?)", [("A", 7, "NQ100", 20260801), ("A", 9, "NQ100", 20260801)])
    connection.commit(); connection.close()
    await atom.start()
    await atom._on_pulse({"official_time": 1000.0})
    return atom, bus


async def test_disabled_no_write():
    print("\n--- test_disabled_no_write ---")
    db = _tmp_db()
    try:
        atom, bus = await _new(False, db)
        await atom._on_command({"account_id": "A", "magic": 20260801, "action": "MODIFY_SL", "ticket": 7, "symbol": "NQ100",
                                "side": "BUY", "stop_loss": 100.0})
        assert _read(db) == [], "مطفأ يجب ألا يكتب"
        h = await atom.health_check()
        assert h.state == HealthState.DEGRADED and h.message == "DISABLED"
        print("OK — enabled=false → لا كتابة + DISABLED")
    finally:
        _cleanup(db)


async def test_modify_written():
    print("\n--- test_modify_written ---")
    db = _tmp_db()
    try:
        atom, bus = await _new(True, db)
        await atom._on_command({"account_id": "A", "magic": 20260801, "action": "MODIFY_SL", "ticket": 7, "symbol": "NQ100",
                                "side": "BUY", "stop_loss": 100.0})
        rows = _read(db)
        assert len(rows) == 1, rows
        assert rows[0][0] == "MODIFY_SL" and rows[0][1] == 7
        assert rows[0][2] == 100.0 and rows[0][4] == "PENDING"
        print("OK — MODIFY_SL كُتب PENDING بـticket=7 sl=100")
    finally:
        _cleanup(db)


async def test_partial_written():
    print("\n--- test_partial_written ---")
    db = _tmp_db()
    try:
        atom, bus = await _new(True, db)
        await atom._on_command({"account_id": "A", "magic": 20260801, "action": "CLOSE_PARTIAL", "ticket": 9, "symbol": "NQ100",
                                "side": "BUY", "volume": 0.1})
        rows = _read(db)
        assert len(rows) == 1 and rows[0][0] == "CLOSE_PARTIAL"
        assert rows[0][1] == 9 and rows[0][3] == 0.1
        print("OK — CLOSE_PARTIAL كُتب volume=0.1")
    finally:
        _cleanup(db)


async def test_bad_action_ignored():
    print("\n--- test_bad_action_ignored ---")
    db = _tmp_db()
    try:
        atom, bus = await _new(True, db)
        await atom._on_command({"action": "OPEN", "ticket": 7})  # not a manage action
        assert _read(db) == []
        print("OK — أمر ليس إدارة → يُتجاهَل")
    finally:
        _cleanup(db)


async def test_health_states():
    print("\n--- test_health_states ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context({"enabled": True, "db_path": db, "magic": 20260801}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة UNHEALTHY→HEALTHY (مفعّل)")
    finally:
        _cleanup(db)


async def test_system_halt_allows_protective_exits():
    # v2.2.0 (ختم ٢٥-٠٨): الإيقاف لا يمنع الحماية والخروج — منعها كان انقلاب
    # سلامة مقيسًا (المالك يوقف فيفقد القدرة على الخروج من السوق). كل أفعال
    # هذه الذرّة تخفض أو تحمي التعرض، فتُكتب أثناء الإيقاف وتُعدّ معلَنة.
    print("\n--- test_system_halt_allows_protective_exits ---")
    db = _tmp_db()
    try:
        atom, bus = await _new(True, db)
        await atom._on_halt({"scope": "SYSTEM", "reason": "OWNER"})
        for _ in range(5):
            await atom._on_command({"account_id": "A", "magic": 20260801,
                                    "action": "MODIFY_SL", "ticket": 7,
                                    "symbol": "NQ100", "side": "BUY", "stop_loss": 100.0})
        assert len(_read(db)) == 5, "الإيقاف لا يمنع أوامر الحماية"
        assert atom._halt_exit_allowed == 5, atom._halt_exit_allowed
        print("OK — إيقاف النظام: 5 أوامر حماية كُتبت وعُدّت (v2.2.0)")
    finally:
        _cleanup(db)


async def test_account_halt_is_scoped():
    print("\n--- test_account_halt_is_scoped ---")
    db = _tmp_db()
    try:
        atom, bus = await _new(True, db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO positions_v2 VALUES (?,?,?,?)", ("B", 7, "NQ100", 20260801))
        conn.commit(); conn.close()
        await atom._on_halt({"account_id": "A", "reason": "RISK"})
        base = {"magic": 20260801, "action": "MODIFY_SL", "ticket": 7,
                "symbol": "NQ100", "side": "BUY", "stop_loss": 100.0}
        await atom._on_command({**base, "account_id": "A"})
        await atom._on_command({**base, "account_id": "B"})
        rows = _read(db)
        # v2.2.0: الحماية تمرّ للحسابين — الموقوف يُعدّ فقط لا يُمنع.
        assert len(rows) == 2, f"الحماية تمرّ حتى للموقوف: {rows}"
        assert atom._halt_exit_allowed == 1, atom._halt_exit_allowed
        print("OK — إيقاف حساب: A يمرّ معدودًا · B يمرّ عاديًّا (v2.2.0)")
    finally:
        _cleanup(db)


async def test_reset_lifts_halt():
    print("\n--- test_reset_lifts_halt ---")
    db = _tmp_db()
    try:
        atom, bus = await _new(True, db)
        base = {"account_id": "A", "magic": 20260801, "action": "MODIFY_SL",
                "ticket": 7, "symbol": "NQ100", "side": "BUY", "stop_loss": 100.0}
        await atom._on_halt({"scope": "SYSTEM", "reason": "OWNER"})
        await atom._on_command(base)
        # v2.2.0: تُكتب أثناء الإيقاف (حماية) وتُعدّ.
        assert len(_read(db)) == 1 and atom._halt_exit_allowed == 1
        await atom._on_reset({"scope": "SYSTEM"})
        await atom._on_command(base)
        # بعد الرفع: تُكتب بلا عدّ إيقاف — العدّاد لا يتحرك.
        assert len(_read(db)) == 2 and atom._halt_exit_allowed == 1
        print("OK — الرفع يمسح حالة الإيقاف والعدّاد يثبت (v2.2.0)")
    finally:
        _cleanup(db)


async def test_halt_without_identity_is_refused():
    # لا نطاق ولا حساب ⇒ لا يُفترض «النظام كله» ولا يُتجاهل بصمت.
    print("\n--- test_halt_without_identity_is_refused ---")
    db = _tmp_db()
    try:
        atom, bus = await _new(True, db)
        await atom._on_halt({"reason": "GHOST"})
        h = await atom.health_check()
        assert h.details.get("halt_identity_blocked") == 1, h.details
        await atom._on_command({"account_id": "A", "magic": 20260801,
                                "action": "MODIFY_SL", "ticket": 7,
                                "symbol": "NQ100", "side": "BUY", "stop_loss": 100.0})
        assert len(_read(db)) == 1, "إيقاف بلا هويّة لا يوقف شيئًا ولا يمرّ صامتًا"
        print("OK — إيقاف بلا هويّة: يُعدّ ولا يُفترض نطاقه")
    finally:
        _cleanup(db)


async def main():
    tests = [test_disabled_no_write, test_modify_written, test_partial_written,
             test_bad_action_ignored, test_health_states,
             test_system_halt_allows_protective_exits, test_account_halt_is_scoped,
             test_reset_lifts_halt, test_halt_without_identity_is_refused]
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
