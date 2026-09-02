"""مزوّد الأخبار — ختم المالك ٢٠٢٦-٠٨-٢٠ (ستّة بنود جودة).

ما كان: ثلاث تغذيات ياهو، **واحدة منها ميتة (404)**، بلا وقت نشر، بلا فلترة،
بلا إزالة تكرار، وبلا وسم أثر — فحقلا `published_at` و`impact_level` يصلان
فارغين إلى قاعدة الجسر واللوحة.

ما صار:
  ① مصادر مجانية بلا مفتاح، **كلّها مُختبَرة حيّة** قبل إدراجها (٢٠٢٦-٠٨-٢٠).
     والرسمي منها (الفيدرالي · مكتب التحليل الاقتصادي) يسبق الصحافة رتبةً:
     المنبع أصدق من التعليق عليه.
  ② وقت النشر الحقيقي من التغذية (`published_parsed`) لا وقت السحب.
  ③ إزالة التكرار بعنوان مُطبَّع — الخبر الواحد من ثلاثة مصادر يصير واحدًا.
  ④ فلترة بالصلة: ما لا يمسّ رموز المالك ولا الاقتصاد الكلّي يُرمى.
  ⑤ وسم أثر **بقاعدة معلَنة** (مصدر + كلمات) — وسبب الوسم يُحفظ معه.
     هذه قاعدة تصنيف، **ليست نبوءة ولا تحليل**.
  ⑥ سبب القبول يُعاد مع الخبر، فلا يمرّ شيء بلا تفسير.

كل خبر مرفوض يُعدّ ويُعلَن رقمه — لا رمي صامت.
"""

from __future__ import annotations

import re
import socket
import time
from calendar import timegm
from typing import Dict, List, Tuple

import feedparser

socket.setdefaulttimeout(12)

# (الاسم · الرابط · رتبة المصدر) — الرتبة: official أعلى من press
# ⚠️ حالة كل تغذية مقيسة يوم ٢٠٢٦-٠٨-٢٠. الميتة أُزيلت ولم تُترك تكذب:
#    BLS = 403 · الخزانة = 404 · ياهو «سوق الأسهم» = 404
FEEDS: List[Tuple[str, str, str]] = [
    ("federal_reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "official"),
    ("bea", "https://apps.bea.gov/rss/rss.xml", "official"),
    ("cnbc_top", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "press"),
    ("cnbc_markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "press"),
    ("marketwatch", "http://feeds.marketwatch.com/marketwatch/topstories/", "press"),
    ("investing", "https://www.investing.com/rss/news_25.rss", "press"),
    ("yahoo_ndx", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5ENDX&region=US&lang=en-US", "press"),
    # مضافة ٢٠٢٦-٠٨-٢٥ بأمر المالك «يورو مهم»: تغذية العملات — المصدر الوحيد
    # الحيّ لأخبار اليورو والإسترليني. مقيسة ساعة الإضافة: HTTP 200 · ١٠ مُدخلات
    # (دولار · لوني · إسترليني)، بلا خبر يورو في تلك اللحظة — وهي ٠٢:٣٠ بإسطنبول،
    # أي خارج ساعات الجلسة الأوروبية. الحكم على غلّتها من اليورو يحتاج نهارًا أوروبيًّا.
    # ⚠️ تغذيات ECB الرسمية جُرّبت ورُفضت: رجعت صفر مُدخل (رابطان، ومع ترويسة
    #    متصفّح)، وFXStreet صفر، وDailyFX ٤٠٣ — لا مصدر يورو رسمي متاح من هنا.
    ("investing_fx", "https://www.investing.com/rss/news_1.rss", "press"),
]

# رموز المالك السبعة وما يرادفها في العناوين
SYMBOL_WORDS = {
    "USTEC": ("nasdaq", "ndx", "nasdaq-100", "nasdaq 100"),
    "US500": ("s&p", "s&p 500", "sp500"),
    "US30": ("dow", "dow jones"),
    "XAUUSD": ("gold", "bullion"),
    "BTCUSD": ("bitcoin", "btc"),
    # كانت ("euro", "eur/usd", "ecb") فقط — والمطابقة بحدود كلمات، فـ«Eurozone»
    # و«European Central Bank» و«Lagarde» كنّ يسقطن «خارج الموضوع» قبل الوسم.
    # ولا تُدرَج «european» وحدها: أسهم أوروبا ليست زوج اليورو/دولار.
    "EURUSD": ("euro", "euros", "eurozone", "euro zone", "euro area", "eur",
               "eur/usd", "eurusd", "ecb", "european central bank", "lagarde"),
    "GBPUSD": ("sterling", "pound", "gbp/usd", "boe"),
}

# كلمات الاقتصاد الكلّي التي تحرّك الدولار
MACRO_WORDS = (
    "fed", "federal reserve", "fomc", "powell", "rate cut", "rate hike",
    "interest rate", "inflation", "cpi", "pce", "payroll", "jobless",
    "unemployment", "gdp", "treasury", "yield", "tariff", "dollar",
    "recession", "stimulus", "debt ceiling",
    "ecb", "european central bank", "eurozone", "euro area", "lagarde",
)

# كلمات الأثر العالي — حدث يحرّك السوق فورًا.
# ⚠️ «downgrade» و«war» مجرّدتين أُزيلتا: بلا حدود كلمات كانت «warns» تُصنَّف
#    حربًا و«Codowngrade» تُصنَّف خفض تصنيف سيادي. البديل عبارات لا لبس فيها.
HIGH_WORDS = (
    "fomc", "rate decision", "rate cut", "rate hike", "powell", "cpi",
    "inflation report", "payrolls", "jobless claims", "gdp", "emergency",
    "sovereign default", "credit downgrade", "sanctions", "tariff", "lagarde",
)

_PUNCT = re.compile(r"[^a-z0-9؀-ۿ ]+")
_SPACES = re.compile(r"\s+")
_WORD_CACHE: dict[str, re.Pattern] = {}


def _has(blob: str, word: str) -> bool:
    """مطابقة بحدود كلمات — لا مطابقة جزئية داخل كلمة أخرى.

    هذا الدرس مقيس: «dow» داخل «downgrade» صنّفت خبر شركة مؤشّرَ داو،
    و«war» داخل «warns» صنّفت تحذيرًا حربًا. الحدود تمنع العائلة كلّها.
    """
    pattern = _WORD_CACHE.get(word)
    if pattern is None:
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")
        _WORD_CACHE[word] = pattern
    return bool(pattern.search(blob))


def _norm(title: str) -> str:
    """عنوان مُطبَّع للمقارنة: حروف صغيرة بلا ترقيم ولا مسافات زائدة."""
    return _SPACES.sub(" ", _PUNCT.sub(" ", title.lower())).strip()


def _published(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return float(timegm(value))
            except (TypeError, ValueError):
                continue
    return None


def _relevance(title: str, summary: str, rank: str) -> Tuple[bool, str]:
    """هل يهمّ هذا الخبر؟ ولماذا — السبب يُعاد دائمًا.

    العنوان أوّلًا: ذكر الرمز في العنوان صلة حقيقية، وذكره في الملخّص قد يكون
    عابرًا. لذلك رموز المالك تُطابَق على العنوان، والكلّي على الاثنين.
    """
    if rank == "official":
        return True, "مصدر رسمي"
    head = title.lower()
    blob = f"{title} {summary}".lower()
    for symbol, words in SYMBOL_WORDS.items():
        for word in words:
            if _has(head, word):
                return True, symbol
    for word in MACRO_WORDS:
        if _has(blob, word):
            return True, word
    return False, ""


def symbols_in(title: str) -> tuple:
    """كل رموز المالك التي يذكرها العنوان — بترتيب ظهورها لا بترتيب القاموس.

    القياس الذي فرضها: `_relevance` تُرجع **أوّل** رمز يطابق ثمّ تتوقّف، وترتيبها
    ترتيب القاموس. فعنوان «Bitcoin has beaten stocks and gold» وصل موسومًا
    `XAUUSD` لأنّ الذهب يسبق البيتكوين في القاموس — والخبر عن البيتكوين.
    وعنوان يذكر «S&P 500» و«Nasdaq» معًا وصل برمز واحد ونصفه ضاع.

    سبب القبول (`why`) يبقى كما هو: واحد، وهو أوّل ما طابق. أمّا الوسم فقائمة.
    """
    head = title.lower()
    found = []
    for symbol, words in SYMBOL_WORDS.items():
        position = None
        for word in words:
            match = _WORD_CACHE.get(word)
            if match is None:
                match = re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")
                _WORD_CACHE[word] = match
            hit = match.search(head)
            if hit and (position is None or hit.start() < position):
                position = hit.start()
        if position is not None:
            found.append((position, symbol))
    return tuple(symbol for _, symbol in sorted(found))


def _impact(title: str, summary: str, rank: str) -> str:
    blob = f"{title} {summary}".lower()
    for word in HIGH_WORDS:
        if _has(blob, word):
            return "HIGH"
    if rank == "official":
        return "HIGH"
    for word in MACRO_WORDS:
        if _has(blob, word):
            return "MEDIUM"
    return "LOW"


class NewsService:
    def __init__(self) -> None:
        self.last_stats: Dict[str, int] = {}

    def fetch_latest_news(self, limit: int = 20) -> List[Dict[str, object]]:
        items: List[Dict[str, object]] = []
        seen: set[str] = set()
        stats = {"fetched": 0, "dropped_dup": 0, "dropped_offtopic": 0, "feeds_dead": 0}

        for source, url, rank in FEEDS:
            try:
                feed = feedparser.parse(url)
            except Exception:
                stats["feeds_dead"] += 1
                continue
            if not getattr(feed, "entries", None):
                stats["feeds_dead"] += 1
                continue

            for entry in feed.entries:
                title = (getattr(entry, "title", "") or "").strip()
                if not title:
                    continue
                stats["fetched"] += 1
                key = _norm(title)[:90]
                if key in seen:
                    stats["dropped_dup"] += 1
                    continue
                summary = (getattr(entry, "summary", "") or "").strip()
                keep, why = _relevance(title, summary, rank)
                if not keep:
                    stats["dropped_offtopic"] += 1
                    continue
                seen.add(key)
                items.append({
                    "title": title,
                    "link": (getattr(entry, "link", "") or "").strip(),
                    "summary": summary[:400],
                    "source": source,
                    "rank": rank,
                    "published_at": _published(entry),
                    "impact_level": _impact(title, summary, rank),
                    "why": why,
                })

        # الأحدث أوّلًا؛ وما لا وقت له يُؤخَّر بدل أن يُختلق له وقت
        items.sort(key=lambda i: (i["published_at"] is not None, i["published_at"] or 0),
                   reverse=True)
        stats["kept"] = min(len(items), limit)
        self.last_stats = stats
        return items[:limit]
