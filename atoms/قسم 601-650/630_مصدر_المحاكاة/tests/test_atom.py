import asyncio
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location(
    "_t630", Path(__file__).resolve().parents[1] / "atom.py")
m = importlib.util.module_from_spec(spec)
sys.modules["_t630"] = m
spec.loader.exec_module(m)


class L:
    def __getattr__(self, n): return lambda *a, **k: None


class B:
    def __init__(self): self.e = []
    def subscribe(self, *a): pass
    async def publish(self, n, p): self.e.append((n, p))


def _make_db(path, ticks=5):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS market_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, provider TEXT,
        bid REAL, ask REAL, occurred_at REAL, payload_json TEXT)""")
    for i in range(ticks):
        ts = 1000.0 + i * 0.5
        payload = f'{{"symbol":"BTCUSD","bid":{64000+i},"ask":{64001+i},"price":{64000.5+i},"timestamp":{ts},"account_id":"A","provider":"CTRADER"}}'
        conn.execute("INSERT INTO market_data (symbol,provider,bid,ask,occurred_at,payload_json) VALUES (?,?,?,?,?,?)",
                     ("BTCUSD", "CTRADER", 64000+i, 64001+i, ts, payload))
    conn.commit()
    conn.close()


async def test_replays_ticks_with_same_shape():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        _make_db(db, ticks=5)
        b = B(); a = m.Atom()
        await a.initialize(m.AtomContext(630, {"db_path": db, "speed": 0.0, "batch_size": 3}, L(), b.publish, b.subscribe))
        await a.start()
        await asyncio.sleep(0.2)
        await a.stop()
        outs = [p for n, p in b.e if n == m.EVENT_OUT]
        assert len(outs) == 5, f"نشر {len(outs)} تِكّة — المتوقع 5"
        first = outs[0]
        assert first["symbol"] == "BTCUSD"
        assert first["provider"] == "REPLAY"
        assert "bid" in first and "ask" in first
        assert isinstance(first.get("timestamp"), (int, float))
        print(f"OK — {len(outs)} تِكّة بنفس شكل 622 (provider=REPLAY)")


async def test_speed_zero_no_delay():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        _make_db(db, ticks=10)
        b = B(); a = m.Atom()
        await a.initialize(m.AtomContext(630, {"db_path": db, "speed": 0, "batch_size": 10}, L(), b.publish, b.subscribe))
        await a.start()
        await asyncio.sleep(0.15)
        await a.stop()
        outs = [p for n, p in b.e if n == m.EVENT_OUT]
        assert len(outs) == 10, f"speed=0 يجب أن ينشر فورًا — {len(outs)}"
        print("OK — speed=0: كل التِكّات فورًا بلا انتظار")


async def test_symbol_filter():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        _make_db(db, ticks=3)
        conn = sqlite3.connect(db)
        for i in range(3):
            conn.execute("INSERT INTO market_data (symbol,provider,bid,ask,occurred_at,payload_json) VALUES (?,?,?,?,?,?)",
                         ("ETH", "MT5", 3000+i, 3001+i, 2000.0+i, '{"symbol":"ETH"}'))
        conn.commit(); conn.close()
        b = B(); a = m.Atom()
        await a.initialize(m.AtomContext(630, {"db_path": db, "symbol": "ETH", "speed": 0}, L(), b.publish, b.subscribe))
        await a.start()
        await asyncio.sleep(0.15)
        await a.stop()
        outs = [p for n, p in b.e if n == m.EVENT_OUT]
        assert all(o["symbol"] == "ETH" for o in outs), "فلتر الرمز لم يعمل"
        assert len(outs) == 3
        print("OK — فلتر الرمز: ETH فقط (3 تِكّات)")


async def test_health_and_missing_db():
    b = B(); a = m.Atom()
    await a.initialize(m.AtomContext(630, {"db_path": "/nonexistent/x.db"}, L(), b.publish, b.subscribe))
    await a.start()
    await asyncio.sleep(0.1)
    h = await a.health_check()
    assert h.message == "REPLAY_DB_NOT_FOUND" or h.details.get("state") == "REPLAY_DB_NOT_FOUND"
    print("OK — بلا قاعدة: DEGRADED برسالة واضحة")


async def main():
    await test_replays_ticks_with_same_shape()
    await test_speed_zero_no_delay()
    await test_symbol_filter()
    await test_health_and_missing_db()
    print("630 replay source tests passed")


if __name__ == "__main__":
    asyncio.run(main())
