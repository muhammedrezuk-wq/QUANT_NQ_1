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
    "_atom707", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom707"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_APPROVED = _mod.EVENT_APPROVED
EVENT_FINAL = _mod.EVENT_FINAL
EVENT_ORDER_BUILT = _mod.EVENT_ORDER_BUILT
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
        return AtomContext(atom_id=707, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


async def _make(bus, db_path, keep_payload=True):
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": db_path, "keep_full_payload": keep_payload}))
    await atom.start()
    return atom


async def test_stores_decision_with_identity():
    print("\n--- test_stores_decision_with_identity ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db)
        await bus.publish(EVENT_APPROVED, {
            "request_id": "r1", "account_id": "A1", "symbol": "NQ100",
            "direction": "BUY", "approved": True, "confidence": 0.8,
            "strategy_id": "breakout", "model_id": "m7", "timestamp": 3.0})
        rows = _query(db, "SELECT stage, account_id, strategy_id, model_id, approved,"
                          " confidence FROM decisions WHERE request_id='r1'")
        assert rows == [("APPROVED", "A1", "breakout", "m7", 1, 0.8)], rows
        print(f"OK — قرار مع مفاتيح الهوية (account/strategy/model — قاعدة ٢٢): {rows[0]}")
    finally:
        os.unlink(db)


async def test_metadata_fallback():
    print("\n--- test_metadata_fallback ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db)
        # decision.approved.state (466) يحمل approved/direction بالميتاداتا لا بالأعلى
        await bus.publish(EVENT_APPROVED, {
            "request_id": "r2", "account_id": "A1", "symbol": "BTCUSD",
            "metadata": {"approved": False, "direction": "sell", "confidence": 0.3}})
        rows = _query(db, "SELECT direction, approved, confidence FROM decisions"
                          " WHERE request_id='r2'")
        assert rows == [("sell", 0, 0.3)], rows
        print(f"OK — استخراج من الميتاداتا احتياطًا (approved/direction/confidence): {rows[0]}")
    finally:
        os.unlink(db)


async def test_multi_stage_by_request():
    print("\n--- test_multi_stage_by_request ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db)
        await bus.publish(EVENT_APPROVED, {"request_id": "r9", "account_id": "A1"})
        await bus.publish(EVENT_ORDER_BUILT, {"request_id": "r9", "account_id": "A1",
                                              "volume": 1.5})
        await bus.publish(EVENT_FINAL, {"request_id": "r9", "account_id": "A1"})
        stages = [r[0] for r in _query(
            db, "SELECT stage FROM decisions WHERE request_id='r9' ORDER BY id")]
        assert stages == ["APPROVED", "ORDER_BUILT", "DECISION_FINALIZED"], stages
        print(f"OK — تتبّع القرار عبر مراحله بنفس request_id: {stages}")
    finally:
        os.unlink(db)


async def test_stores_decision_link_columns():
    print("\n--- test_stores_decision_link_columns ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db)
        await bus.publish(EVENT_APPROVED, {
            "request_id": "r5", "account_id": "A1", "symbol": "NQ100",
            "decision_id": "dec:5", "gate_request_id": "gate:5"})
        rows = _query(db, "SELECT decision_id, gate_request_id FROM decisions"
                          " WHERE request_id='r5'")
        assert rows == [("dec:5", "gate:5")], rows
        print(f"OK — عمودا الربط decision_id/gate_request_id يُخزَّنان: {rows[0]}")
    finally:
        os.unlink(db)


async def test_link_columns_absent_stay_null():
    print("\n--- test_link_columns_absent_stay_null ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        await _make(bus, db)
        await bus.publish(EVENT_APPROVED, {"request_id": "r6", "account_id": "A1"})
        rows = _query(db, "SELECT decision_id, gate_request_id FROM decisions"
                          " WHERE request_id='r6'")
        assert rows == [(None, None)], rows
        print("OK — بلا هوية بالحمولة: العمودان NULL صريحة -- لا اختراع")
    finally:
        os.unlink(db)


async def test_migrates_existing_database_without_link_columns():
    print("\n--- test_migrates_existing_database_without_link_columns ---")
    db = _tmp_db()
    try:
        # قاعدة قديمة بمخطط بلا العمودين الجديدين إطلاقًا -- المهاجرة
        # الحقيقية: عمود يُضاف لجدول قائم فيه صفوف، بلا فقد بيانات.
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT NOT NULL,
            request_id TEXT, account_id TEXT, symbol TEXT, direction TEXT,
            approved INTEGER, reason TEXT, confidence REAL, strategy_id TEXT,
            model_id TEXT, volume REAL, stop_loss REAL, take_profit REAL,
            payload_json TEXT, decided_at REAL)""")
        conn.execute("INSERT INTO decisions (stage, request_id, account_id) "
                     "VALUES ('APPROVED', 'old1', 'A1')")
        conn.commit(); conn.close()

        bus = FakeEventBus()
        atom = await _make(bus, db)
        columns = {row[1] for row in _query(db, "PRAGMA table_info(decisions)")}
        assert {"decision_id", "gate_request_id"} <= columns, columns
        old_row = _query(db, "SELECT request_id, decision_id, gate_request_id"
                             " FROM decisions WHERE request_id='old1'")
        assert old_row == [("old1", None, None)], old_row
        print("OK — الصفّ القديم نجا من الهجرة والعمودان الجديدان NULL له")

        await bus.publish(EVENT_APPROVED, {
            "request_id": "new1", "account_id": "A1",
            "decision_id": "dec:9", "gate_request_id": "gate:9"})
        new_row = _query(db, "SELECT decision_id, gate_request_id FROM decisions"
                             " WHERE request_id='new1'")
        assert new_row == [("dec:9", "gate:9")], new_row
        print("OK — بعد الهجرة: صف جديد يخزّن العمودين بلا مشكلة")

        # محاكاة إعادة تشغيل: مثيل ثانٍ على نفس القاعدة المهاجَرة أصلًا --
        # ALTER TABLE على عمود موجود مسبقًا يجب ألا يفشل.
        atom2 = Atom()
        await atom2.initialize(bus.make_context({"db_path": db, "keep_full_payload": True}))
        await atom2.start()
        health = await atom2.health_check()
        assert health.details["store_ready"] is True, health.details
        print("OK — إعادة التشغيل على قاعدة مهاجَرة أصلًا لا تفشل (العمود موجود مسبقًا)")
    finally:
        os.unlink(db)


async def test_health_states():
    print("\n--- test_health_states ---")
    db = _tmp_db()
    try:
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(
            {"db_path": db, "keep_full_payload": True}))
        assert (await atom.health_check()).state == HealthState.UNHEALTHY
        await atom.start()
        assert (await atom.health_check()).state == HealthState.DEGRADED
        await bus.publish(EVENT_APPROVED, {"request_id": "r1", "account_id": "A1"})
        assert (await atom.health_check()).state == HealthState.HEALTHY
        print("OK — الصحة: UNHEALTHY -> DEGRADED -> HEALTHY")
    finally:
        os.unlink(db)


async def main():
    tests = [test_stores_decision_with_identity, test_metadata_fallback,
             test_multi_stage_by_request, test_health_states,
             test_stores_decision_link_columns, test_link_columns_absent_stay_null,
             test_migrates_existing_database_without_link_columns]
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
