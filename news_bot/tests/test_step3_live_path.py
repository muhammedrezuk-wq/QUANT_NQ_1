"""اختبارات المسار الحيّ — الأخطاء المُصلَحة يوم ٢٠٢٦-٠٨-٢٤.

لماذا هذا الملفّ: الاختبارات السابقة (٨١ اختبارًا) كانت كلّها تحرس `rss_writer`
و`news.db` — فرعًا غير موصول بـ`main.py` (نُقل إلى الأرشيف يوم ٢٠٢٦-٠٨-٢٤).
أمّا المسار الذي يخدم المستخدمين فعلًا (تصنيف · أجندة · حالة · كتابة الجسر)
فكان بلا اختبار واحد — وهذا الملفّ يغطّيه.

كل صنف هنا يقفل خطأً مقيسًا لا يُخترع من الخيال — واسم الاختبار يقول أيّه.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge_writer as bw
from app import scheduler as sched
from app.analysis_service import AnalysisService, has_term
from app.calendar_bridge import CalendarBridge
from app.news_service import FEEDS, _impact, _relevance, symbols_in
from app.jsonio import write_json_atomic
from app.translator import TranslatorService, looks_translated


class TestKeywordBoundaries(unittest.TestCase):
    """«against» كانت تُقرأ «gain» فيُصنَّف خبر الرسوم الجمركية إيجابيًّا."""

    def test_a_keyword_inside_another_word_does_not_match(self):
        self.assertFalse(has_term("us imposes tariffs against china", "gain"))
        self.assertFalse(has_term("the company downgraded its outlook", "drop"))

    def test_real_inflections_still_match(self):
        self.assertTrue(has_term("nasdaq gains ground", "gain"))
        self.assertTrue(has_term("stocks surging after the open", "surge"))
        self.assertTrue(has_term("oil dropped sharply", "drop"))
        self.assertTrue(has_term("yields falling", "fall"))

    def test_tariff_headline_is_no_longer_scored_positive(self):
        verdict = AnalysisService().score_headline("US imposes tariffs against China")
        self.assertNotIn("إيجابي", verdict)

    def test_a_genuinely_positive_headline_is_still_positive(self):
        verdict = AnalysisService().score_headline("Nasdaq rallies as earnings beat estimates")
        self.assertIn("إيجابي", verdict)


class TestAtomicJson(unittest.TestCase):
    """`users.json` مبتور = بوت بلا مالك. الكتابة إمّا تتمّ أو لا تبدأ."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "users.json")

    def test_it_writes_and_rewrites_and_leaves_no_temp_files(self):
        write_json_atomic(self.path, {"owner_chat_id": 1, "allowed_users": [1]}, indent=2)
        write_json_atomic(self.path, {"owner_chat_id": 2, "allowed_users": [2]}, indent=2)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["owner_chat_id"], 2)
        self.assertEqual(os.listdir(self.dir), ["users.json"])

    def test_a_failed_write_leaves_the_old_file_untouched(self):
        write_json_atomic(self.path, {"owner_chat_id": 7}, indent=2)
        with self.assertRaises(TypeError):
            write_json_atomic(self.path, {"bad": object()}, indent=2)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["owner_chat_id"], 7)
        self.assertEqual(os.listdir(self.dir), ["users.json"])


class TestBridgeWriterCounts(unittest.TestCase):
    """`ON CONFLICT DO UPDATE` يُرجع rowcount=1 للتحديث أيضًا — فكانت الحالة
    تقول «جديد ٣٠» بعد كل سحبة ولو لم يصل خبر واحد."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(bw._NEWS_TABLE)
        bw.ensure_columns(self.con)
        self.item = {"title": "Fed holds rates", "link": "l", "source": "cnbc_top",
                     "impact_level": "HIGH", "published_at": 1.0, "summary": "s", "why": "fed"}

    def test_first_write_is_new(self):
        self.assertEqual(bw.write_news(self.con, [self.item], 1.0), (1, 0))

    def test_same_headline_again_is_not_counted_new(self):
        bw.write_news(self.con, [self.item], 1.0)
        self.assertEqual(bw.write_news(self.con, [self.item], 2.0), (0, 1))

    def test_only_the_unseen_headline_counts_as_new(self):
        bw.write_news(self.con, [self.item], 1.0)
        other = dict(self.item, title="CPI comes in hot")
        self.assertEqual(bw.write_news(self.con, [self.item, other], 3.0), (1, 1))


class TestSymbolsColumn(unittest.TestCase):
    """الذرّة ٦١٦ في QUANT_NQ تقرأ عمود `symbols` وتُرفقه بالخبر — ولم يكن
    موجودًا في جدول الجسر، فتصل الأخبار بلا رمز رغم أنّنا نعرفه."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(bw._NEWS_TABLE)
        bw.ensure_columns(self.con)

    def _symbols(self, why: str) -> str:
        item = {"title": "خبر " + why, "link": "l", "source": "s", "impact_level": "LOW",
                "published_at": 1.0, "summary": "", "why": why}
        bw.write_news(self.con, [item], 1.0)
        row = self.con.execute(
            "SELECT symbols FROM news WHERE headline = ?", (item["title"],)).fetchone()
        return row[0]

    def test_the_column_exists_after_ensure_columns(self):
        columns = {row[1] for row in self.con.execute("PRAGMA table_info(news)")}
        self.assertIn("symbols", columns)

    def test_an_owner_symbol_is_carried_through(self):
        self.assertEqual(self._symbols("USTEC"), "USTEC")
        self.assertEqual(self._symbols("XAUUSD"), "XAUUSD")

    def test_a_macro_match_leaves_symbols_empty_rather_than_guessing(self):
        # «لا نعرف الرمز» ليست «لا رمز له» — و٦١٦ تحذف الحقل عند الفراغ.
        self.assertEqual(self._symbols("fed"), "")
        self.assertEqual(self._symbols("مصدر رسمي"), "")

    def _tag(self, title: str) -> str:
        item = {"title": title, "link": "l", "source": "s", "impact_level": "LOW",
                "published_at": 1.0, "summary": "", "why": "USTEC"}
        bw.write_news(self.con, [item], 1.0)
        return self.con.execute(
            "SELECT symbols FROM news WHERE headline = ?", (title,)).fetchone()[0]

    def test_a_headline_naming_two_symbols_carries_both(self):
        # ٦١٦ تقسم الحقل على الفاصلة، فالقائمة تصل رمزين لا رمزًا واحدًا.
        self.assertEqual(self._tag("S&P 500, Nasdaq End Lower On Chip Weakness"),
                         "US500,USTEC")

    def test_the_order_follows_the_headline_not_the_dictionary(self):
        # الخطأ المقيس: «Bitcoin … and gold» وصل موسومًا XAUUSD وحده لأنّ الذهب
        # يسبق البيتكوين في ترتيب القاموس — والخبر عن البيتكوين.
        self.assertEqual(self._tag("Bitcoin has beaten stocks and gold this year"),
                         "BTCUSD,XAUUSD")

    def test_symbols_in_ignores_macro_only_headlines(self):
        self.assertEqual(symbols_in("Fed holds rates steady as inflation cools"), ())


class TestEuroCoverage(unittest.TestCase):
    """أمر المالك ٢٠٢٦-٠٨-٢٥: «يورو مهم». وكانت مفرداته ثلاثًا فقط
    (`euro` · `eur/usd` · `ecb`) والمطابقة بحدود كلمات — فيسقط أغلب خبره
    «خارج الموضوع» قبل أن يُوسم، وقاعدة الجسر فيها صفر صفّ EURUSD."""

    def test_the_words_that_used_to_be_dropped_are_now_kept(self):
        for title in ("Eurozone inflation cools to 2.1% in August",
                      "European Central Bank signals caution",
                      "Lagarde says policy remains restrictive",
                      "EUR/USD steadies near 1.09"):
            keep, why = _relevance(title, "", "press")
            self.assertTrue(keep, title)
            self.assertEqual(why, "EURUSD", title)
            self.assertEqual(symbols_in(title), ("EURUSD",), title)

    def test_european_stocks_are_not_the_euro_pair(self):
        # لا نوسّع حتى نكذب: أسهم أوروبا ليست زوج اليورو/دولار.
        self.assertEqual(symbols_in("European stocks open higher"), ())

    def test_the_ecb_chief_ranks_like_the_fed_chief(self):
        self.assertEqual(_impact("Lagarde signals a pause", "", "press"), "HIGH")

    def test_the_currency_feed_is_registered(self):
        self.assertIn("investing_fx", {name for name, _url, _rank in FEEDS})


class TestTranslationGuard(unittest.TestCase):
    """جوجل ردّ بصفحة خطأ نصًّا عاديًّا، فقُبلت ترجمةً وخُزّنت على القرص —
    فوصل «Error 500 (Server Error)» إلى الطالب مكان الخبر (مقيس ٢٠٢٦-٠٨-٢٥)."""

    ERROR_PAGE = "Error 500 (Server Error)!!1500.That\u2019s an error.There was an error."

    def setUp(self):
        # \u0639\u0632\u0644 \u062a\u0627\u0645\u0651 \u0639\u0646 \u0630\u0627\u0643\u0631\u0629 \u0627\u0644\u062a\u0631\u062c\u0645\u0629 \u0627\u0644\u062d\u0642\u064a\u0642\u064a\u0629: \u062a\u0634\u063a\u064a\u0644\u0629 \u0633\u0627\u0628\u0642\u0629 \u0644\u0647\u0630\u0647 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631\u0627\u062a
        # \u0643\u062a\u0628\u062a \u0641\u064a\u0647\u0627 \u062a\u0631\u062c\u0645\u0627\u062a \u0627\u062e\u062a\u0628\u0627\u0631\u064a\u0629 (\u0645\u0642\u064a\u0633)\u060c \u0641\u0635\u0627\u0631\u062a \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631\u0627\u062a \u062a\u0642\u0631\u0623 \u0645\u0627 \u0643\u062a\u0628\u062a\u0647
        # \u0647\u064a \u0644\u0627 \u0645\u0627 \u064a\u0641\u0639\u0644\u0647 \u0627\u0644\u0643\u0648\u062f. \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631 \u0627\u0644\u0630\u064a \u064a\u0644\u0648\u0651\u062b \u0628\u064a\u0627\u0646\u0627\u062a \u0627\u0644\u0645\u0633\u062a\u062e\u062f\u0645 \u0644\u064a\u0633 \u0627\u062e\u062a\u0628\u0627\u0631\u064b\u0627.
        import app.translator as translator_module
        self._module = translator_module
        self._old_path = translator_module.CACHE_PATH
        self._old_cache = TranslatorService._cache
        translator_module.CACHE_PATH = os.path.join(tempfile.mkdtemp(), "cache.json")
        TranslatorService._cache = None

    def tearDown(self):
        self._module.CACHE_PATH = self._old_path
        TranslatorService._cache = self._old_cache

    def test_an_error_page_is_not_a_translation(self):
        self.assertFalse(looks_translated(self.ERROR_PAGE))

    def test_english_output_is_not_an_arabic_translation(self):
        self.assertFalse(looks_translated("Nasdaq ends lower"))

    def test_real_arabic_passes(self):
        self.assertTrue(looks_translated("ناسداك يغلق منخفضًا"))

    def test_a_poisoned_reply_is_neither_shown_nor_cached(self):
        service = TranslatorService()
        service.engines = [("stub", _PoisonEngine())]          # بلا شبكة
        original = "Nasdaq ends lower on chip weakness"
        out = service.translate_text(original)
        self.assertEqual(out, original)                       # يُعاد الأصل
        self.assertNotIn(original, TranslatorService._load())  # ولا يُخزَّن شيء

    def test_the_chain_moves_to_the_next_engine_when_one_is_poisoned(self):
        # مقيس ٢٠٢٦-٠٨-٢٥: جوجل ردّ 500 على كل طلب، فبقي الخبر إنجليزيًّا أمام
        # طالب عربي. المحرّك التالي في السلسلة يمسك بدل أن يسقط الخبر.
        service = TranslatorService()
        service.engines = [("poison", _PoisonEngine()), ("good", _ArabicEngine())]
        self.assertEqual(service.translate_text("Fed holds rates"), "الفيدرالي يثبّت الفائدة")
        self.assertEqual(service.last_engine, "good")


class _PoisonEngine:
    """\u0645\u062d\u0631\u0651\u0643 \u064a\u0631\u062f\u0651 \u0635\u0641\u062d\u0629 \u062e\u0637\u0623 \u2014 \u0643\u0645\u0627 \u0641\u0639\u0644 \u062c\u0648\u062c\u0644 \u0641\u0639\u0644\u064b\u0627 \u064a\u0648\u0645 \u0662\u0660\u0662\u0666-\u0660\u0668-\u0662\u0665."""

    def translate(self, text):
        return "Error 500 (Server Error)!!1500.That\u2019s an error."


class _ArabicEngine:
    def translate(self, text):
        return "\u0627\u0644\u0641\u064a\u062f\u0631\u0627\u0644\u064a \u064a\u062b\u0628\u0651\u062a \u0627\u0644\u0641\u0627\u0626\u062f\u0629"


class _StubTranslator:
    def translate_text(self, text):
        return "ترجمة: %s" % text[:20]


class _StubBot:
    def __init__(self):
        self.translator = _StubTranslator()

    def format_news_message(self, item, next_event=""):
        return "%s | %s" % (item.get("title_ar"), next_event)


class TestAutoPush(unittest.TestCase):
    """أمر المالك: «عدد أخبار منشورة ووقت دقيق». البوت كان لا ينشر من نفسه."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.dir
        os.environ["NQ_BRIDGE_DB"] = os.path.join(self.dir, "missing.db")  # بلا تقويم
        os.environ["NEWS_PUSH"] = "on"
        os.environ["NEWS_PUSH_MIN_IMPACT"] = "HIGH"
        os.environ["NEWS_PUSH_MAX_PER_RUN"] = "2"
        fresh = time.time() - 120          # خبر عمره دقيقتان
        self.items = [
            {"title": "Fed delivers emergency rate cut", "impact_level": "HIGH",
             "summary": "s", "link": "l", "source": "cnbc_top", "why": "fed",
             "published_at": fresh},
            {"title": "CPI comes in hot", "impact_level": "HIGH",
             "summary": "s", "link": "l", "source": "cnbc_top", "why": "cpi",
             "published_at": fresh},
            {"title": "Some small cap moves", "impact_level": "LOW",
             "summary": "s", "link": "l", "source": "yahoo_ndx", "why": "USTEC",
             "published_at": fresh},
        ]

    def tearDown(self):
        for key in ("DATA_DIR", "NQ_BRIDGE_DB", "NEWS_PUSH",
                    "NEWS_PUSH_MIN_IMPACT", "NEWS_PUSH_MAX_PER_RUN"):
            os.environ.pop(key, None)

    def _run(self, bot_obj, users=(10,)):
        fake = _FakeBot()
        state = _FakeState()
        asyncio.run(sched.push_news(_FakeApplication(fake), _FakeStorage(list(users)),
                                    state, bot_obj, self.items))
        return fake, state

    def test_nothing_is_pushed_while_the_switch_is_off(self):
        os.environ["NEWS_PUSH"] = "off"
        fake, _ = self._run(_StubBot())
        self.assertEqual(fake.sent, [])

    def test_only_high_impact_is_pushed_and_capped(self):
        fake, state = self._run(_StubBot())
        self.assertEqual(len(fake.sent), 2)                    # السقف احتُرم
        self.assertNotIn("Some small cap", fake.sent[0][1])    # المنخفض لم يُنشر
        self.assertIn("last_news_push", state.values)

    def test_a_headline_with_no_publish_time_is_never_auto_pushed(self):
        # قياس المالك ٢٠٢٦-٠٨-٢٥: خبر بلا وقت كان يُختم بلحظة سحبه فيُقرأ
        # «وقع الآن» — ووُجد خبر عمره ثلاثة أسابيع بهذا الختم. النشر التلقائي
        # يقول «الآن»، فلا يقولها عمّا لا يعرف وقته. (يبقى متاحًا بـ/news.)
        for item in self.items:
            item["published_at"] = None
        fake, _ = self._run(_StubBot())
        self.assertEqual(fake.sent, [])

    def test_stale_news_is_not_announced_as_breaking(self):
        for item in self.items:
            item["published_at"] = time.time() - 21 * 86400   # ثلاثة أسابيع
        fake, _ = self._run(_StubBot())
        self.assertEqual(fake.sent, [])

    def test_freshness_is_measured_not_assumed(self):
        now = time.time()
        self.assertTrue(sched.is_fresh({"published_at": now - 600}, now, 7200))
        self.assertFalse(sched.is_fresh({"published_at": now - 99999}, now, 7200))
        self.assertFalse(sched.is_fresh({"published_at": None}, now, 7200))
        self.assertFalse(sched.is_fresh({}, now, 7200))

    def test_the_same_headline_is_never_pushed_twice(self):
        self._run(_StubBot())
        fake, _ = self._run(_StubBot())
        self.assertEqual(fake.sent, [])

    def test_a_headline_that_reached_nobody_is_retried_next_cycle(self):
        failing = _FakeBot(fail=True)
        asyncio.run(sched.push_news(_FakeApplication(failing), _FakeStorage([10]),
                                    _FakeState(), _StubBot(), self.items))
        fake, _ = self._run(_StubBot())
        self.assertEqual(len(fake.sent), 2)                    # لم يُختم، فأُعيد


class TestNewsMessageShape(unittest.TestCase):
    """القارئ يجب أن يخرج بشيء: ملخّص · رمز · توقّع منشور · وقت النشر."""

    def _message(self, next_event="16:00 — قرار الفائدة · المتوقّع 4.25 · السابق 4.50"):
        from app.telegram_bot import TelegramBot
        bot = TelegramBot.__new__(TelegramBot)
        bot.analysis_service = AnalysisService()
        item = {"title": "Nasdaq slides as Fed signals higher rates",
                "title_ar": "ناسداك يتراجع مع إشارات الفيدرالي",
                "summary_ar": "تراجع المؤشّر بعد إشارات إلى إبقاء الفائدة مرتفعة.",
                "impact_level": "HIGH", "source": "cnbc_markets",
                "published_at": 1787600000.0, "link": "https://example.com/x"}
        return bot.format_news_message(item, next_event)

    def test_the_summary_reaches_the_reader(self):
        # كان الملخّص يُسحب ويُخزَّن ولا يُعرض أبدًا.
        self.assertIn("تراجع المؤشّر بعد إشارات", self._message())

    def test_the_symbols_are_named(self):
        self.assertIn("USTEC", self._message())

    def test_the_expected_figure_comes_from_the_calendar_not_from_us(self):
        message = self._message()
        self.assertIn("ما يُترقَّب", message)
        self.assertIn("المتوقّع 4.25", message)

    def test_a_missing_publish_time_is_declared_not_hidden(self):
        # الحذف الصامت يجعل القارئ يفترض «الآن» — وهو ما يفعله المستهلك
        # الخارجي فعلًا حين يجد الحقل فارغًا (مقيس على ٢٠ صفًّا من ٠٦-٠٨).
        from app.telegram_bot import TelegramBot
        bot = TelegramBot.__new__(TelegramBot)
        bot.analysis_service = AnalysisService()
        item = {"title": "Gold climbs", "title_ar": "الذهب يصعد", "impact_level": "LOW",
                "source": "cnbc_top", "published_at": None, "link": "x"}
        message = bot.format_news_message(item)
        self.assertIn("وقت النشر غير معلن", message)

    def test_no_expectation_line_when_the_calendar_has_nothing(self):
        self.assertNotIn("ما يُترقَّب", self._message(next_event=""))


def _bridge_db(path: str, rows: list) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE calendar (id INTEGER, title TEXT, country TEXT, currency TEXT,"
        " impact_level TEXT, scheduled_at REAL, actual TEXT, forecast TEXT,"
        " previous TEXT, written_at REAL)")
    con.executemany("INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


class TestCalendarMarking(unittest.TestCase):
    """كانت الرسالة تُختم «مُرسَلة» قبل إرسالها: سقوط الشبكة = ضياعها للأبد."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        db = os.path.join(self.dir, "nq_brain.db")
        soon = time.time() + 300  # حدث بعد خمس دقائق ⇒ تنبيهه مستحقّ الآن
        _bridge_db(db, [(1, "قرار الفائدة", "US", "USD", "HIGH", soon, "", "5.0", "5.0", 0)])
        os.environ["NQ_BRIDGE_DB"] = db
        os.environ["CAL_CURRENCY"] = "USD"
        os.environ["CAL_MIN_IMPACT"] = "MEDIUM"
        os.environ["CAL_ALERT_MINUTES"] = "15"
        self.bridge = CalendarBridge(data_dir=self.dir)

    def tearDown(self):
        os.environ.pop("NQ_BRIDGE_DB", None)

    def test_reading_due_messages_does_not_mark_them_sent(self):
        first = self.bridge.due_announcements()
        self.assertEqual(len(first), 1)
        again = self.bridge.due_announcements()
        self.assertEqual([k for k, _ in again], [k for k, _ in first])

    def test_marking_sent_stops_the_repeat(self):
        due = self.bridge.due_announcements()
        self.bridge.mark_sent([key for key, _ in due])
        self.assertEqual(self.bridge.due_announcements(), [])

    def test_the_sent_ledger_survives_a_restart(self):
        due = self.bridge.due_announcements()
        self.bridge.mark_sent([key for key, _ in due])
        self.assertEqual(CalendarBridge(data_dir=self.dir).due_announcements(), [])


class _FakeBot:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list = []

    async def send_message(self, chat_id, text):
        if self.fail:
            raise ConnectionError("getaddrinfo failed")
        self.sent.append((chat_id, text))


class _FakeApplication:
    def __init__(self, bot):
        self.bot = bot


class _FakeStorage:
    def __init__(self, users):
        self._users = users

    def list_users(self):
        return list(self._users)


class _FakeState:
    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value

    def touch(self, key):
        self.values[key] = "touched"


class _FakeBridge:
    instances: list = []

    def __init__(self):
        self.marked = []
        _FakeBridge.instances.append(self)

    def due_announcements(self):
        return [("pre:1", "تنبيه")]

    def mark_sent(self, keys):
        self.marked.extend(keys)


class TestAnnounceCalendar(unittest.TestCase):
    def setUp(self):
        _FakeBridge.instances = []
        self._real = sched.CalendarBridge
        sched.CalendarBridge = _FakeBridge

    def tearDown(self):
        sched.CalendarBridge = self._real

    def test_a_delivered_message_is_marked_sent(self):
        bot = _FakeBot()
        state = _FakeState()
        asyncio.run(sched.announce_calendar(_FakeApplication(bot), _FakeStorage([10]), state))
        self.assertEqual(_FakeBridge.instances[0].marked, ["pre:1"])
        self.assertEqual(len(bot.sent), 1)

    def test_a_message_that_reached_nobody_is_not_marked_sent(self):
        state = _FakeState()
        asyncio.run(sched.announce_calendar(_FakeApplication(_FakeBot(fail=True)),
                                            _FakeStorage([10]), state))
        self.assertEqual(_FakeBridge.instances[0].marked, [])
        self.assertIn("last_error", state.values)


class TestStatusCommand(unittest.TestCase):
    """`/status` كان يقرأ المفتاح `event` بينما الجسر يسمّيه `title` ⇒
    KeyError في اللحظة التي وُجد لأجلها الأمر بالضبط."""

    def _run_status(self, active_event):
        from app.telegram_bot import TelegramBot

        replies = []

        class _Message:
            async def reply_text(self, text, **kwargs):
                replies.append(text)

        class _Chat:
            id = 55

        class _Update:
            message = _Message()
            effective_chat = _Chat()

        class _Calendar:
            def get_active_lock_event(self):
                return active_event

        class _Storage:
            def is_allowed(self, chat_id):
                return True

        # يُنشأ الكائن بلا `__init__`: الاختبار على سلوك الأمر، لا على تركيب
        # الخدمات (شبكة وقواعد بيانات لا شأن لها بهذا الخطأ).
        bot = TelegramBot.__new__(TelegramBot)
        bot.calendar_service = _Calendar()
        bot.storage = _Storage()
        asyncio.run(bot.status(_Update(), None))
        return replies

    def test_an_active_lock_names_the_event_instead_of_crashing(self):
        replies = self._run_status({"title": "قرار الفائدة", "time": "21:00", "impact": "HIGH"})
        self.assertEqual(len(replies), 1)
        self.assertIn("قرار الفائدة", replies[0])
        self.assertIn("21:00", replies[0])

    def test_no_lock_says_so_plainly(self):
        replies = self._run_status(None)
        self.assertIn("لا يوجد Event Lock", replies[0])


if __name__ == "__main__":
    unittest.main()
