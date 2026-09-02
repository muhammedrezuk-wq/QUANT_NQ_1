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

os.environ.pop("NQ_BRIDGE_DB", None)  # اختبار محكم — لا يلمس جسر الحقيقي

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom616", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom616"] = _mod
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

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=616, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _make_db(path, news=(), calendar=(), tables=True):
    con = sqlite3.connect(path)
    if tables:
        con.execute("CREATE TABLE news (id INTEGER PRIMARY KEY, headline TEXT, "
                    "link TEXT, source TEXT, sentiment_score REAL, "
                    "impact_level TEXT, published_at REAL, written_at REAL)")
        con.execute("CREATE TABLE calendar (id TEXT, title TEXT, country TEXT, "
                    "currency TEXT, impact_level TEXT, scheduled_at REAL, "
                    "actual TEXT, forecast TEXT, previous TEXT, written_at REAL)")
        con.executemany("INSERT INTO news VALUES (?,?,?,?,?,?,?,?)", news)
        con.executemany("INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)", calendar)
    con.commit()
    con.close()


def _cfg(db):
    return {"db_path": db, "poll_interval_s": 30, "batch_limit": 50}


async def _make(db):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(_cfg(db)))
    await atom.start()
    return bus, atom


async def _pulse(atom, t):
    await atom._on_pulse({"official_time": float(t)})
    if atom._read_task is not None:
        await atom._read_task


def _of(bus, name):
    return [p for n, p in bus.published if n == name]


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "bridge.db")


async def test_news_publish_and_shape():
    print("\n--- test_news_publish_and_shape ---")
    db = _db_path()
    _make_db(db, news=[(1, "CPI beats", "http://x", "yahoo", None, "HIGH", 1000.0, 1001.0),
                       (2, "Tech rally", None, "yahoo", None, None, 1002.0, 1003.0)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    news = _of(bus, "market.news")
    assert len(news) == 2, len(news)
    assert news[0]["headline"] == "CPI beats" and news[0]["source"] == "yahoo"
    assert news[0]["id"] == 1 and news[0]["published_at"] == 1000.0
    print(f"OK — نشر خبرين: {news[0]['headline']}")


async def test_calendar_publish_and_shape():
    print("\n--- test_calendar_publish_and_shape ---")
    db = _db_path()
    _make_db(db, calendar=[("e1", "NFP", "US", "USD", "HIGH", 5000.0,
                            "200K", "180K", "175K", 5001.0)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    cal = _of(bus, "market.calendar")
    assert len(cal) == 1 and cal[0]["title"] == "NFP" and cal[0]["currency"] == "USD"
    print(f"OK — نشر حدث أجندة: {cal[0]['title']} {cal[0]['actual']}")


async def test_missing_tables_no_crash():
    print("\n--- test_missing_tables_no_crash ---")
    db = _db_path()
    _make_db(db, tables=False)  # قاعدة فاضية — لا جدولين
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    assert not _of(bus, "market.news") and not _of(bus, "market.calendar")
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED and h.message == "NO_ROWS_YET", h.message
    print("OK — جدول ناقص → NO_ROWS_YET بلا كسر")


async def test_news_dedup_by_id():
    print("\n--- test_news_dedup_by_id ---")
    db = _db_path()
    _make_db(db, news=[(1, "a", None, "y", None, None, 1.0, 1.0),
                       (2, "b", None, "y", None, None, 2.0, 2.0)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    n1 = len(_of(bus, "market.news"))
    assert atom._last_news_id == 2
    await _pulse(atom, 200.0)  # نفس الصفوف · id>2 → لا جديد
    assert len(_of(bus, "market.news")) == n1
    print("OK — إسقاط تكرار الأخبار بالمعرّف")


async def test_calendar_dedup_by_written_at():
    print("\n--- test_calendar_dedup_by_written_at ---")
    db = _db_path()
    _make_db(db, calendar=[("e1", "NFP", "US", "USD", "HIGH", 5000.0,
                            None, "180K", "175K", 10.0)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    c1 = len(_of(bus, "market.calendar"))
    await _pulse(atom, 200.0)  # نفس written_at → لا إعادة
    assert len(_of(bus, "market.calendar")) == c1
    print("OK — إسقاط تكرار الأجندة بـwritten_at")


async def test_health_states():
    print("\n--- test_health_states ---")
    db = _db_path()
    _make_db(db, news=[(1, "a", None, "y", None, None, 1.0, 1.0)])
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(_cfg(db)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await _pulse(atom, 100.0)
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_no_symbols_column_safe_fallback():
    # باعتماد المالك بند 22 حزمة أ — ق٧: غياب عمود symbols سقوط آمن لا انهيار ولا ادّعاء.
    print("\n--- test_no_symbols_column_safe_fallback ---")
    db = _db_path()
    _make_db(db, news=[(1, "CPI beats", None, "yahoo", None, "HIGH", 1000.0, 1001.0)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    news = _of(bus, "market.news")
    assert len(news) == 1
    assert "symbols" not in news[0], "عمود غائب = حقل غائب، لا قائمة فارغة مدّعاة"
    assert news[0]["impact_level"] == "HIGH"
    print("OK — بلا عمود symbols: نشر سليم والحقل غائب بصدق")


async def test_symbols_column_passthrough():
    print("\n--- test_symbols_column_passthrough ---")
    db = _db_path()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE news (id INTEGER PRIMARY KEY, headline TEXT, "
                "link TEXT, source TEXT, sentiment_score REAL, "
                "impact_level TEXT, symbols TEXT, published_at REAL, "
                "written_at REAL)")
    con.executemany(
        "INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?)",
        [(1, "CPI beats", None, "yahoo", None, "HIGH", "EURUSD,USDJPY", 1000.0, 1001.0),
         (2, "Vague news", None, "yahoo", None, None, "UNKNOWN", 1002.0, 1003.0)])
    con.commit()
    con.close()
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    news = _of(bus, "market.news")
    assert len(news) == 2
    assert news[0]["symbols"] == ["EURUSD", "USDJPY"], news[0]
    assert "symbols" not in news[1], "UNKNOWN = جهل لا قائمة فارغة"
    print("OK — عمود symbols موجود: يُفكّ بالفواصل ويُمرَّر، وUNKNOWN يبقى جهلًا")


async def test_no_impact_column_safe_fallback():
    print("\n--- test_no_impact_column_safe_fallback ---")
    db = _db_path()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE news (id INTEGER PRIMARY KEY, headline TEXT, "
                "link TEXT, source TEXT, sentiment_score REAL, "
                "published_at REAL, written_at REAL)")
    con.execute("INSERT INTO news VALUES (1, 'CPI beats', NULL, 'yahoo', NULL, 1000.0, 1001.0)")
    con.commit()
    con.close()
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    news = _of(bus, "market.news")
    assert len(news) == 1
    assert news[0]["impact_level"] == "UNKNOWN", news[0]
    print("OK — بلا عمود impact_level: UNKNOWN معلَن بلا انهيار")


async def main():
    tests = [test_news_publish_and_shape, test_calendar_publish_and_shape,
             test_missing_tables_no_crash, test_news_dedup_by_id,
             test_calendar_dedup_by_written_at, test_health_states,
             test_no_symbols_column_safe_fallback,
             test_symbols_column_passthrough,
             test_no_impact_column_safe_fallback]
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
