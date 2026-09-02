import ast
import asyncio
import os
import sqlite3
import sys
import tempfile
import time as _time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("NQ_BRIDGE_DB", None)

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_ATOM_PATH = _Path(__file__).resolve().parents[1] / "atom.py"
_spec = _ilu.spec_from_file_location("_atom618", _ATOM_PATH)
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom618"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_TICK = _mod.EVENT_TICK
EVENT_SPECS = _mod.EVENT_SPECS
utc_gate = _mod.utc_gate
broker_clock = _mod.broker_clock
_CLOCK_TOLERANCE_S = _mod._CLOCK_TOLERANCE_S

NOW = 1_786_791_577.281
HOUR3 = 10800.0


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
        cfg = {"db_path": db_path, "table_name": "ticks_v2", "spec_table": "symbol_specs_v2",
               "spec_refresh_s": 300, "poll_interval_s": 0.1, "batch_limit": 500,
               "delete_consumed": True, "max_age_s": 30}
        return AtomContext(atom_id=618, config=cfg, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ticks_v2 (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT, bid REAL, ask REAL,"
                 " last REAL, volume REAL, tick_ms REAL)")
    conn.execute("CREATE TABLE symbol_specs_v2 (account_id TEXT, symbol TEXT, contract_size REAL, tick_value REAL, tick_size REAL)")
    conn.commit()
    conn.close()


def _add_tick(path, row_id, symbol="NQ", bid=100.0, ask=100.5, tick_ms=1700000000000.0):
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO ticks_v2 (id, account_id, symbol, bid, ask, tick_ms) VALUES (?, ?, ?, ?, ?, ?)",
                 (row_id, "A", symbol, bid, ask, tick_ms))
    conn.commit()
    conn.close()


def _pending_ticks(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT count(*) FROM ticks_v2").fetchone()[0]
    finally:
        conn.close()


class _Now:
    def __init__(self, value):
        self.value = value
        self._orig = None

    def __enter__(self):
        self._orig = _mod.time.time
        _mod.time.time = lambda: self.value
        return self

    def __exit__(self, *exc):
        _mod.time.time = self._orig


async def _drain_ms(tick_ms, now=NOW, symbol="BTCUSD"):
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "b.db")
        _make_db(db)
        _add_tick(db, 1, symbol=symbol, tick_ms=tick_ms)
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        with _Now(now):
            await atom._drain_once()
        ticks = [p for n, p in bus.published if n == EVENT_TICK]
        return ticks, atom


async def _publishes_feed_tick_from_bridge():
    print("\n--- test_publishes_feed_tick_from_bridge ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "b.db")
        _make_db(db)
        _add_tick(db, 1)
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        await atom._drain_once()
        ticks = [p for n, p in bus.published if n == EVENT_TICK]
        assert len(ticks) == 1
        assert ticks[0]["symbol"] == "NQ" and ticks[0]["provider"] == "MT5"
        assert ticks[0]["broker_timestamp"] == 1700000000.0, "ساعة الوسيط الخام"
        assert ticks[0]["broker_timestamp_raw"] == 1700000000.0
        assert ticks[0]["broker_clock_offset_s"] is not None, "الانحراف يُقاس ويُعلَن"
        assert ticks[0]["exchange_timestamp"] is None, "ساعة لا توافق الاستلام لا تُقدَّم ختمًا"
        assert ticks[0]["timestamp"] == ticks[0]["received_at"]
        assert ticks[0]["timestamp_source"] == "received"
        assert ticks[0]["clock_domain"] == "UTC"
        print(f"OK — نشر feed.mt5.tick محليًا من الجسر (بلا شبكة): {ticks[0]['symbol']}")


async def _deletes_consumed_ticks():
    print("\n--- test_deletes_consumed_ticks ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "b.db")
        _make_db(db)
        _add_tick(db, 1)
        _add_tick(db, 2)
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        await atom._drain_once()
        assert _pending_ticks(db) == 0, "الطابور يتنظّف بعد النشر"
        print("OK — حذف التكّات المقروءة (طابور مو مخزن)")


async def _drops_incomplete_tick():
    print("\n--- test_drops_incomplete_tick ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "b.db")
        _make_db(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO ticks_v2 (id, account_id, symbol, tick_ms) VALUES (1, 'A', 'NQ', 1700000000000.0)")
        conn.commit()
        conn.close()
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        await atom._drain_once()
        assert not [p for n, p in bus.published if n == EVENT_TICK]
        assert atom.dropped_count == 1
        print("OK — تكّة ناقصة أُسقطت بلا انهيار")


async def _publishes_symbol_specs():
    print("\n--- test_publishes_symbol_specs ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "b.db")
        _make_db(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO symbol_specs_v2 VALUES ('A', 'NQ', 20.0, 0.5, 0.25)")
        conn.commit()
        conn.close()
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        await atom._refresh_specs()
        specs = [p for n, p in bus.published if n == EVENT_SPECS]
        assert specs and specs[0]["symbols"][0]["contract_size"] == 20.0
        assert specs[0]["published_at"] and specs[0]["symbols"][0]["spec_observed_monotonic"] > 0
        print("OK — نشر مواصفات الرموز (contract_size)")


def test_13_three_hour_broker_clock_falls_back_to_received():
    g = utc_gate(NOW + HOUR3, NOW)
    assert g["exchange_timestamp"] is None
    assert g["timestamp"] == NOW
    assert g["timestamp_source"] == "received"
    assert g["clock_domain"] == "UTC"
    assert g["clock_valid"] is False
    assert abs(g["clock_offset_s"] - HOUR3) < 1e-9
    assert g["broker_timestamp_raw"] == NOW + HOUR3
    print("618 — ١٣: UTC+3 خام → timestamp=received_at لا ±10800")


def test_14_aligned_broker_stamp_accepted():
    g = utc_gate(NOW + 0.4, NOW)
    assert g["exchange_timestamp"] == NOW + 0.4
    assert g["timestamp"] == NOW + 0.4
    assert g["timestamp_source"] == "broker"
    assert g["clock_domain"] == "UTC"
    assert g["clock_valid"] is True
    print("618 — ١٤: طابع موافق الاستلام → timestamp=broker")


def test_15_small_0_7s_offset_is_valid():
    g = utc_gate(NOW - 0.7, NOW)
    assert g["clock_valid"] is True
    assert g["timestamp_source"] == "broker"
    assert g["timestamp"] == NOW - 0.7
    assert g["exchange_timestamp"] == NOW - 0.7
    print("618 — ١٥: انحراف 0.7ث VALID")


def test_16_beyond_tolerance_uses_received():
    g = utc_gate(NOW + _CLOCK_TOLERANCE_S + 0.01, NOW)
    assert g["exchange_timestamp"] is None
    assert g["timestamp"] == NOW
    assert g["timestamp_source"] == "received"
    assert g["clock_valid"] is False
    print("618 — ١٦: فوق التسامح → received لا تصحيح يدوي")


def test_22_missing_received_rejects():
    assert utc_gate(NOW, float("nan")) is None
    assert utc_gate(NOW, 0.0) is None
    assert utc_gate(NOW, -1.0) is None
    print("618 — ٢٢: بلا received_at صالح → REJECT")


async def _13_payload_not_stale_from_timezone():
    ticks, atom = await _drain_ms((NOW + HOUR3) * 1000.0, now=NOW, symbol="BTCUSD")
    assert len(ticks) == 1, ticks
    t = ticks[0]
    assert t["exchange_timestamp"] is None
    assert t["timestamp"] == NOW
    assert t["received_at"] == NOW
    assert t["timestamp_source"] == "received"
    assert t["clock_domain"] == "UTC"
    age = t["received_at"] - NOW
    assert abs(age) < 1e-9
    assert t["timestamp"] != t["broker_timestamp_raw"]
    print("618 — ١٣ عبر الجسر: ثلاث ساعات لا تصير STALE")


async def _14_payload_aligned():
    ticks, _ = await _drain_ms(NOW * 1000.0, now=NOW, symbol="USTEC")
    t = ticks[0]
    assert t["timestamp_source"] == "broker"
    assert t["exchange_timestamp"] == NOW
    assert t["timestamp"] == NOW
    assert t["clock_domain"] == "UTC"
    print("618 — ١٤ عبر الجسر: طابع صالح UTC")


async def _18_order_follows_received_not_corrupt_broker():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "b.db")
        _make_db(db)
        _add_tick(db, 1, symbol="XAUUSD", tick_ms=(NOW + HOUR3) * 1000.0)
        _add_tick(db, 2, symbol="XAUUSD", tick_ms=(NOW + HOUR3 - 50) * 1000.0)
        _add_tick(db, 3, symbol="XAUUSD", tick_ms=(NOW + HOUR3 + 90) * 1000.0)
        bus = FakeEventBus()
        atom = Atom()
        await atom.initialize(bus.make_context(db))
        seq = iter([NOW, NOW + 0.01, NOW + 0.02])
        orig = _mod.time.time
        _mod.time.time = lambda: next(seq)
        try:
            await atom._drain_once()
        finally:
            _mod.time.time = orig
        ticks = [p for n, p in bus.published if n == EVENT_TICK]
        assert [t["source_row_id"] for t in ticks] == [1, 2, 3]
        stamps = [t["timestamp"] for t in ticks]
        assert stamps == [NOW, NOW + 0.01, NOW + 0.02]
        assert all(t["timestamp_source"] == "received" for t in ticks)
        print("618 — ١٨: الترتيب received/id لا ينقلب بطابع خادم فاسد")


def test_12_source_forbids_timezone_conversion():
    src = _ATOM_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    text = ast.unparse(tree)
    assert "10800" not in src
    assert "datetime.now" not in src
    assert "timezone" not in src.lower()
    assert "UTC+3" not in src
    assert '"exchange_timestamp": broker_stamp' not in src
    print("618 — ١٢: لا ±10800 ولا تحويل منطقة في المصدر")


def test_broker_clock_contract_still_holds():
    received = NOW
    offset, exchange = broker_clock(received + HOUR3, received)
    assert abs(offset - HOUR3) < 0.01 and exchange is None
    offset, exchange = broker_clock(received + 0.4, received)
    assert abs(offset - 0.4) < 0.01 and exchange == received + 0.4
    print("618 — عقد ساعة الوسيط: 10800 معلن بلا ختم كاذب")


def test_publishes_feed_tick_from_bridge():
    asyncio.run(_publishes_feed_tick_from_bridge())


def test_deletes_consumed_ticks():
    asyncio.run(_deletes_consumed_ticks())


def test_drops_incomplete_tick():
    asyncio.run(_drops_incomplete_tick())


def test_publishes_symbol_specs():
    asyncio.run(_publishes_symbol_specs())


def test_13_payload_not_stale_from_timezone():
    asyncio.run(_13_payload_not_stale_from_timezone())


def test_14_payload_aligned():
    asyncio.run(_14_payload_aligned())


def test_18_order_follows_received_not_corrupt_broker():
    asyncio.run(_18_order_follows_received_not_corrupt_broker())


async def main():
    tests = [
        _publishes_feed_tick_from_bridge,
        _deletes_consumed_ticks,
        _drops_incomplete_tick,
        _publishes_symbol_specs,
        test_13_three_hour_broker_clock_falls_back_to_received,
        test_14_aligned_broker_stamp_accepted,
        test_15_small_0_7s_offset_is_valid,
        test_16_beyond_tolerance_uses_received,
        test_22_missing_received_rejects,
        _13_payload_not_stale_from_timezone,
        _14_payload_aligned,
        _18_order_follows_received_not_corrupt_broker,
        test_12_source_forbids_timezone_conversion,
        test_broker_clock_contract_still_holds,
    ]
    failed = []
    for t in tests:
        try:
            result = t()
            if asyncio.iscoroutine(result):
                await result
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


def test_618_paper_utc_gate():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
