"""Bridge writer — pulls real news from this app's provider and writes it into
the shared MT5 bridge database (nq_brain.db) so the core's atom 616 reads it
and publishes market.news -> 108. No mocks: real headlines only.

The bot writes, 616 reads, neither imports the other (same pattern as the MT5
expert advisor -> nq_brain.db -> 618).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.news_service import SYMBOL_WORDS, NewsService, symbols_in  # noqa: E402

DEFAULT_DB = r"C:\Users\NQ\AppData\Roaming\MetaQuotes\Terminal\Common\Files\nq_brain.db"
SOURCE = "yahoo_rss"
FETCH_LIMIT = 20

_NEWS_TABLE = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline TEXT UNIQUE,
    link TEXT,
    source TEXT,
    sentiment_score REAL,
    impact_level TEXT,
    published_at REAL,
    written_at REAL
)
"""

# كان `INSERT OR IGNORE`: الخبر الموجود يُتخطّى كلّيًّا، فتبقى حقوله الجديدة
# (الملخّص · سبب القبول · وقت النشر · وسم الأثر) فارغة إلى الأبد.
# الآن: يُدرَج الجديد، ويُكمَّل الناقص في القديم — **ولا يُدهَس شيء موجود**.
_INSERT = (
    "INSERT INTO news "
    "(headline, link, source, sentiment_score, impact_level, published_at, written_at, "
    "summary, relevance, symbols) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(headline) DO UPDATE SET "
    "  summary      = COALESCE(NULLIF(news.summary, ''),   excluded.summary), "
    "  relevance    = COALESCE(NULLIF(news.relevance, ''), excluded.relevance), "
    "  symbols      = COALESCE(NULLIF(news.symbols, ''),   excluded.symbols), "
    "  impact_level = COALESCE(news.impact_level,          excluded.impact_level), "
    "  published_at = COALESCE(news.published_at,          excluded.published_at), "
    "  link         = COALESCE(NULLIF(news.link, ''),      excluded.link)"
)

# عمودان مضافان ٢٠٢٦-٠٨-٢٠ بختم `NQ`:
#   summary   — ملخّص الخبر كما وصل من التغذية
#   relevance — سبب قبوله (رمز المالك أو الكلمة الكلّية التي طابقت)
# آمنان على القارئين: الذرّة ٦١٦ تختار **أعمدة مسمّاة** لا `SELECT *`،
# فإضافة عمود لا تُزحزح شيئًا عندها (فُحص قبل التنفيذ).
# وعمود ثالث مضاف ٢٠٢٦-٠٨-٢٤: `symbols`.
#   الذرّة ٦١٦ تقرأ هذا العمود صراحةً وتُرفقه بالخبر إن وُجد — ولم يكن موجودًا،
#   فتصل الأخبار للمشروع الثاني بلا رمز رغم أنّنا نعرف الرمز فعلًا.
#   يُملأ **فقط** برموز المالك السبعة؛ ومطابقة الاقتصاد الكلّي (fed · cpi …)
#   تبقى فارغة: «لا نعرف الرمز» شيء، و«لا رمز له» شيء آخر — ولا نخلطهما.
_EXTRA_COLUMNS = (("summary", "TEXT"), ("relevance", "TEXT"), ("symbols", "TEXT"))


def symbol_of(item: dict) -> str:
    """كل رموز المالك التي يذكرها العنوان، مفصولة بفواصل — أو نصّ فارغ.

    قائمة لا رمزًا واحدًا: الذرّة ٦١٦ تقسم هذا الحقل على الفاصلة، وخبر يذكر
    ناسداك وS&P معًا يخصّ الاثنين. والاقتصاد الكلّي (fed · cpi) يبقى فارغًا:
    «لا نعرف الرمز» ليست «لا رمز له».
    """
    found = symbols_in(str(item.get("title") or ""))
    if found:
        return ",".join(found)
    why = str(item.get("why") or "").strip()
    return why if why in SYMBOL_WORDS else ""


def ensure_columns(connection: sqlite3.Connection) -> None:
    existing = {row[1] for row in connection.execute("PRAGMA table_info(news)")}
    for name, kind in _EXTRA_COLUMNS:
        if name not in existing:
            connection.execute("ALTER TABLE news ADD COLUMN %s %s" % (name, kind))
    connection.commit()


def _db_path() -> str:
    return os.environ.get("NQ_BRIDGE_DB", "").strip() or DEFAULT_DB


def write_news(connection: sqlite3.Connection, items: list, now: float) -> tuple[int, int]:
    """يكتب الخبر بحقوله الحقيقية، ويعيد (جديد، مُكمَّل).

    كان يكتب `source` ثابتًا و`published_at` و`impact_level` **فارغين** —
    فتظهر اللوحة «—» مكان الوقت، ويبقى وسم الأثر خانةً ميّتة.
    الآن يُكتب المصدر الفعلي، ووقت النشر من التغذية، ووسم الأثر بقاعدته.
    `sentiment_score` يبقى فارغًا عمدًا: **لا نحسبه، فلا ندّعيه.**

    وعدّاد «جديد» كان يعدّ التحديث إدراجًا: `ON CONFLICT DO UPDATE` يُرجع
    `rowcount = 1` في الحالتين (مقيس)، فتقول الحالة «جديد 30» بعد كل سحبة
    ولو لم يصل خبر واحد. الآن يُسأل الجدول قبل الكتابة، فيُفصل الجديد عن
    المُكمَّل — والرقم يعني ما يقوله.
    """
    inserted = 0
    updated = 0
    for item in items:
        headline = (item.get("title") or "").strip()
        if not headline:
            continue
        existed = connection.execute(
            "SELECT 1 FROM news WHERE headline = ?", (headline,)
        ).fetchone() is not None
        cursor = connection.execute(
            _INSERT,
            (headline,
             item.get("link") or "",
             item.get("source") or SOURCE,
             None,                              # sentiment — غير محسوب
             item.get("impact_level"),
             item.get("published_at"),
             now,
             (item.get("summary") or "")[:400],
             item.get("why") or "",
             symbol_of(item)),
        )
        if not existed:
            inserted += 1
        elif cursor.rowcount:
            updated += 1
    connection.commit()
    return inserted, updated


def run_once() -> None:
    db_path = _db_path()
    connection = sqlite3.connect(db_path, timeout=5.0)
    try:
        connection.execute("PRAGMA busy_timeout=3000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(_NEWS_TABLE)
        ensure_columns(connection)
        limit = int(sys.argv[1]) if len(sys.argv) > 1 else FETCH_LIMIT
        items = NewsService().fetch_latest_news(limit)
        now = time.time()
        new_rows, updated_rows = write_news(connection, items, now)
        total = connection.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        print("db=%s" % db_path)
        print("fetched=%d new_written=%d updated=%d total_in_db=%d"
              % (len(items), new_rows, updated_rows, total))
        for row in connection.execute(
                "SELECT id, headline FROM news ORDER BY id DESC LIMIT 5"):
            print("  #%d %s" % (row[0], row[1]))
    finally:
        connection.close()


if __name__ == "__main__":
    run_once()
