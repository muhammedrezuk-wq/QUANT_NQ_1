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

os.environ.pop("NQ_NEWS_DB", None)  # اختبار محكم — لا يلمس قاعدة الأخبار الحقيقيّة

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom615", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom615"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

_COLUMNS = ("id", "dedupe_key", "headline_ar", "headline_src", "lang_src",
            "translated", "link", "source", "source_kind", "scope", "symbols",
            "rule_score", "rule_evidence", "rule_version", "model_score",
            "model_confidence", "model_version", "merge_method", "merge_weights",
            "merge_version", "sentiment_score", "sentiment_state", "impact_level",
            "status", "published_at", "written_at")


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
        return AtomContext(atom_id=615, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


_IMPACT_DEFAULT = object()  # sentinel: distinguishes "unset -> UNKNOWN" from an explicit None (real SQL NULL)


def _row(row_id, scope="UNRESOLVED", symbols="UNKNOWN", headline="Fed holds",
         published=1000.0, written=1001.0, sentiment=None, state="UNKNOWN",
         impact=_IMPACT_DEFAULT):
    return (row_id, "key-%d" % row_id, None, headline, "en", 0,
            "https://x.invalid/%d" % row_id, "yahoo-ndx", "RSS", scope, symbols,
            None, None, "NOT_APPLIED", None, None, "NOT_APPLIED",
            "NOT_APPLIED", "NOT_APPLIED", "NOT_APPLIED",
            sentiment, state, ("UNKNOWN" if impact is _IMPACT_DEFAULT else impact),
            "RECEIVED", published, written)


def _make_db(path, rows=(), table=True):
    con = sqlite3.connect(path)
    if table:
        con.execute("CREATE TABLE news (%s)" % ", ".join(
            "%s %s" % (name, "INTEGER" if name in ("id", "translated") else
                       ("REAL" if name in ("rule_score", "model_score",
                                           "model_confidence", "sentiment_score",
                                           "published_at", "written_at") else "TEXT"))
            for name in _COLUMNS))
        con.executemany(
            "INSERT INTO news VALUES (%s)" % ", ".join("?" for _ in _COLUMNS), rows)
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
    # لا انتظار لمهمّة منفصلة: النبضة نفسها تقرأ وتنشر وتنتهي (ق13).
    await atom._on_pulse({"official_time": float(t)})


def _of(bus, name):
    return [p for n, p in bus.published if n == name]


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "news.db")


async def test_unresolved_never_reaches_the_narrow_event():
    print("\n--- test_unresolved_never_reaches_the_narrow_event ---")
    db = _db_path()
    _make_db(db, rows=[_row(1), _row(2)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    assert len(_of(bus, "market.news.enriched")) == 2
    assert _of(bus, "market.news") == [], "خبر مجهول النطاق عبر إلى مسار التحليل"
    assert atom._unresolved_held == 2
    e = _of(bus, "market.news.enriched")[0]
    assert e["scope"] == "UNRESOLVED" and e["symbols"] == [] and e["forwarded"] is False
    print("OK — UNRESOLVED يُحفظ ويُعرض ولا يدخل market.news")


async def test_resolved_row_reaches_both_events():
    print("\n--- test_resolved_row_reaches_both_events ---")
    db = _db_path()
    _make_db(db, rows=[_row(1, scope="ASSET", symbols="USTEC")])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    narrow = _of(bus, "market.news")
    assert len(narrow) == 1 and narrow[0]["symbols"] == ["USTEC"]
    # v1.2.0: "id" is namespaced by atom (615:<row_id>) -- 616 reads a
    # separate database with its own independent row-id sequence.
    assert narrow[0]["headline"] == "Fed holds" and narrow[0]["id"] == "615:1"
    assert len(_of(bus, "market.news.enriched")) == 1
    assert atom._unresolved_held == 0
    print("OK — المحلول يمرّ بالحدثين، والرموز قائمة لا نصّ")


async def test_missing_impact_level_is_the_shared_unknown_literal():
    """Item 21/27 of the 27-atom review ("same event name, two conflicting
    shapes -- between two atoms, not within one"): a NULL impact_level
    used to reach market.news as Python None here, while 616's own bridge
    always sends the literal string "UNKNOWN" for the same situation on
    the SAME shared event -- a consumer would see two different "we
    don't know" markers depending on which atom produced the event.
    Aligned to 616's explicit, named constant on both enriched and narrow
    (they must agree with each other too, not just with 616)."""
    print("\n--- test_missing_impact_level_is_the_shared_unknown_literal ---")
    db = _db_path()
    _make_db(db, rows=[_row(1, scope="ASSET", symbols="USTEC", impact=None)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    narrow = _of(bus, "market.news")[0]
    enriched = _of(bus, "market.news.enriched")[0]
    assert narrow["impact_level"] == "UNKNOWN", (
        "impact_level غائب يجب أن يصل UNKNOWN حرفيًّا كما ٦١٦ لا None: %r" % narrow["impact_level"])
    assert enriched["impact_level"] == "UNKNOWN", (
        "الحمولة الموسّعة يجب أن تتّفق مع الضيّقة أيضًا: %r" % enriched["impact_level"])
    print("OK — impact_level الغائب UNKNOWN حرفيًّا بكلا الحمولتين، يتّفق مع ٦١٦")


async def test_multiple_symbols_are_split():
    print("\n--- test_multiple_symbols_are_split ---")
    db = _db_path()
    _make_db(db, rows=[_row(1, scope="SECTOR", symbols="USTEC,XAUUSD")])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    assert _of(bus, "market.news")[0]["symbols"] == ["USTEC", "XAUUSD"]
    print("OK — رافد بأكثر من رمز يُقسَم صحيحًا")


async def test_enriched_carries_the_full_contract():
    print("\n--- test_enriched_carries_the_full_contract ---")
    db = _db_path()
    _make_db(db, rows=[_row(1)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    e = _of(bus, "market.news.enriched")[0]
    for field in ("dedupe_key", "headline_src", "link", "source_kind",
                  "rule_version", "model_version", "merge_method",
                  "merge_weights", "merge_version", "sentiment_state",
                  "status", "translated", "headline_ar"):
        assert field in e, field
    assert e["rule_version"] == "NOT_APPLIED" and e["status"] == "RECEIVED"
    assert e["headline_ar"] is None and e["translated"] == 0
    assert e["link"] == "https://x.invalid/1", "الرابط ضاع — وهو سبب وجود الحدث الغنيّ"
    print("OK — الحدث الغنيّ يحمل العقد كاملًا ومعه الرابط")


async def test_rows_are_not_republished():
    print("\n--- test_rows_are_not_republished ---")
    db = _db_path()
    _make_db(db, rows=[_row(1), _row(2)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    first = len(_of(bus, "market.news.enriched"))
    assert atom._last_news_id == 2
    await _pulse(atom, 200.0)
    assert len(_of(bus, "market.news.enriched")) == first
    print("OK — لا إعادة نشر لنفس الصفوف")


async def test_no_detached_task_is_created():
    print("\n--- test_no_detached_task_is_created ---")
    db = _db_path()
    _make_db(db, rows=[_row(1)])
    bus, atom = await _make(db)
    before = len(asyncio.all_tasks())
    await atom._on_pulse({"official_time": 100.0})
    assert not hasattr(atom, "_read_task"), "بقيت مهمّة منفصلة بالذرّة"
    assert len(asyncio.all_tasks()) == before, "النبضة خلّفت مهمّة وراءها"
    assert len(_of(bus, "market.news.enriched")) == 1, "النبضة انتهت قبل النشر"
    print("OK — النبضة تقرأ وتنشر وتنتهي بلا مهمّة خلفيّة")


async def test_poll_interval_is_respected():
    print("\n--- test_poll_interval_is_respected ---")
    db = _db_path()
    _make_db(db, rows=[_row(1)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    reads = atom._reads
    await _pulse(atom, 110.0)  # داخل المهلة ⇒ لا قراءة ثانية
    assert atom._reads == reads, "قرأ قبل انقضاء المهلة"
    print("OK — النبضة داخل المهلة لا تُحدث قراءة")


async def test_missing_database_is_declared_not_crashed():
    print("\n--- test_missing_database_is_declared_not_crashed ---")
    db = os.path.join(tempfile.mkdtemp(), "absent.db")
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED and h.message == "SOURCE_UNAVAILABLE", h.message
    assert not os.path.exists(db), "الجسر أنشأ قاعدة بدل أن يعلن غيابها"
    print("OK — غياب مشروع الأخبار يُعلَن ولا يُنشئ قاعدة ولا يكسر")


async def test_missing_table_no_crash():
    print("\n--- test_missing_table_no_crash ---")
    db = _db_path()
    _make_db(db, table=False)
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED and h.message == "NO_ROWS_YET", h.message
    print("OK — جدول ناقص → NO_ROWS_YET بلا كسر")


async def test_bridge_connection_cannot_write():
    print("\n--- test_bridge_connection_cannot_write ---")
    db = _db_path()
    _make_db(db, rows=[_row(1)])
    con = _mod._bridge_connect(db)
    try:
        failed = False
        try:
            con.execute("DELETE FROM news")
        except sqlite3.OperationalError:
            failed = True
        assert failed, "الجسر يستطيع الكتابة في قاعدة مشروع آخر"
    finally:
        con.close()
    print("OK — وصلة الجسر ترفض الكتابة فعليًّا")


async def test_missing_publish_time_leaves_the_event_without_a_time():
    # ⛔ هذا الاختبار كان يحرس السلوك المعاكس باسم
    #    test_timestamp_falls_back_to_written_at — أي أنّ الغلط كان مكتوبًا عقدًا.
    #    بحكم المالك ٢٠٢٦-٠٨-٢٥: وقت كتابتنا ليس وقت الحدث. الخبر بلا وقت نشر
    #    يبقى بلا وقت، ووقت السحب يمرّ باسمه الصريح written_at لا متنكّرًا ختمًا.
    print("\n--- test_missing_publish_time_leaves_the_event_without_a_time ---")
    db = _db_path()
    _make_db(db, rows=[_row(1, published=None, written=2222.0)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    e = _of(bus, "market.news.enriched")[0]
    assert e["published_at"] is None
    assert "timestamp" not in e
    assert e["written_at"] == 2222.0
    print("OK — الخبر بلا وقت نشر يبقى بلا ختم، ووقت السحب معلَن باسمه")


async def test_publish_time_present_stamps_the_event():
    print("\n--- test_publish_time_present_stamps_the_event ---")
    db = _db_path()
    _make_db(db, rows=[_row(1, published=1111.0, written=2222.0)])
    bus, atom = await _make(db)
    await _pulse(atom, 100.0)
    e = _of(bus, "market.news.enriched")[0]
    assert e["published_at"] == 1111.0 and e["timestamp"] == 1111.0
    assert e["written_at"] == 2222.0
    print("OK — وقت النشر موجود فهو الختم، ووقت السحب منفصل عنه")


async def test_health_states():
    print("\n--- test_health_states ---")
    db = _db_path()
    _make_db(db, rows=[_row(1)])
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(_cfg(db)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await _pulse(atom, 100.0)
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_unresolved_never_reaches_the_narrow_event,
             test_resolved_row_reaches_both_events,
             test_missing_impact_level_is_the_shared_unknown_literal,
             test_multiple_symbols_are_split,
             test_enriched_carries_the_full_contract,
             test_rows_are_not_republished,
             test_no_detached_task_is_created,
             test_poll_interval_is_respected,
             test_missing_database_is_declared_not_crashed,
             test_missing_table_no_crash,
             test_bridge_connection_cannot_write,
             test_missing_publish_time_leaves_the_event_without_a_time,
             test_publish_time_present_stamps_the_event,
             test_health_states]
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
