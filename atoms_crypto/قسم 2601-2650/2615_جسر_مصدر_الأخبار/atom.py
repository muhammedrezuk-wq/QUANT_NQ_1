from __future__ import annotations

import asyncio
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.0"

EVENT_PULSE = "SYS_SECOND"
EVENT_NEWS = "market.news"

_HTTP_TIMEOUT_S = 15.0
_USER_AGENT = "Mozilla/5.0"
# scalping/mexc_news.py — نفس المصدرين حرفيًّا، لا اختراع.
_DEFAULT_FEEDS = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
)

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "AWAITING_FIRST_POLL"


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
        return response.read()


def _parse_items(raw: bytes) -> list[tuple[str, float]]:
    """[(العنوان، طابع نشرٍ يونكسي)] — يتجاهل عناصر بلا عنوانٍ أو تاريخ صالح."""
    root = ET.fromstring(raw)
    out: list[tuple[str, float]] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pub_date = item.findtext("pubDate")
        if not title or not pub_date:
            continue
        try:
            dt = parsedate_to_datetime(pub_date)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out.append((title, dt.timestamp()))
    return out


class Atom(AtomBase):
    """جسر مصدر الأخبار — يجلب عناوين CoinDesk/CoinTelegraph مباشرةً عبر RSS.

    **إصدارٌ ثانٍ كامل الاستبدال، لا تعديلٌ على v1.x.** v1.x كانت تقرأ قاعدة
    مشروعٍ خارجيّ (`ASMAR_NEWS`) بمسار `C:\\Users\\NQ\\...` — تحقّقتُ مباشرة
    (٢٠٢٦-٠٨-٢٨): هذا المسار **غير موجودٍ على هذا الجهاز إطلاقًا**، ولا حتى
    `C:\\Users\\NQ` نفسه. مشروع `ASMAR_NEWS` حقيقيٌّ وبُني بقرار المالك (راجع
    `التاريخ.md`)، لكنه غير مهيَّأ بهذه البيئة تحديدًا. بأمر المستخدم المباشر
    ("ابحث بمجلد scalping تلاقيهم") استُبدلت الآلية بالكامل بـ`scalping/
    mexc_news.py` — سكربتٌ مستقلٌّ موجودٌ فعلًا وقيد الاستخدام هناك، بلا أي
    قاعدة بياناتٍ خارجية: طلب HTTP مباشر لتغذيتَي RSS، تحليل XML، عمر كل عنوانٍ
    بالدقائق. **العقد الموسَّع (`market.news.enriched`) أُسقط بالكامل** — كان
    يحمل حقول ترجمةٍ وتسجيل مشاعر/نموذج لم تعد موجودة المصدر أصلًا (كانت تأتي
    من معالجة `ASMAR_NEWS` نفسها) ولا مستهلك له بالشجرة سوى هذه الذرّة ذاتها
    (تحقّقتُ: صفر ذرّةٍ أخرى تشتركه). ينضبط بنفس انضباط v1.x الصريح: لا
    `asyncio.create_task` — النبضة تنتظر القراءة والنشر كاملَين قبل أن تعود،
    بلا مهمّةٍ خلفيةٍ تُخلَّف وراءها (نفس حكم المالك السابق بحرفيته)."""

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._feeds: tuple[tuple[str, str], ...] = _DEFAULT_FEEDS
        self._poll_interval_s = 900.0
        self._last_poll_at = 0.0
        self._polling = False
        self._seen: deque[tuple[str, str]] = deque(maxlen=400)
        self._polls = 0
        self._published = 0
        self._feed_errors: dict[str, str] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        feeds_cfg = cfg.get("feeds")
        if isinstance(feeds_cfg, list) and feeds_cfg:
            self._feeds = tuple((str(f["name"]), str(f["url"])) for f in feeds_cfg)
        self._poll_interval_s = float(cfg.get("poll_interval_s", 900.0))
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _poll_feeds(self) -> list[tuple[str, str, float]]:
        """خيطٌ منفصل (I/O شبكةٍ حاجب) — [(المصدر، العنوان، طابع النشر)]."""
        out: list[tuple[str, str, float]] = []
        for name, url in self._feeds:
            try:
                items = _parse_items(_fetch(url))
                self._feed_errors.pop(name, None)
            except Exception as exc:   # شبكة أو XML فاسد — كلاهما "غير متاح الآن"
                self._feed_errors[name] = type(exc).__name__
                continue
            for title, published_at in items:
                out.append((name, title, published_at))
        return out

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        now = time.time()
        if self._polling or (now - self._last_poll_at) < self._poll_interval_s:
            return
        self._last_poll_at = now
        self._polling = True
        try:
            items = await asyncio.to_thread(self._poll_feeds)
            self._polls += 1
            for source, title, published_at in items:
                key = (source, title)
                if key in self._seen:
                    continue
                self._seen.append(key)
                self._published += 1
                await self._context.publish(EVENT_NEWS, {
                    "headline": title, "source": source,
                    "published_at": published_at, "timestamp": published_at,
                })
        finally:
            self._polling = False

    async def health_check(self) -> HealthStatus:
        details = {"polls": self._polls, "published": self._published,
                   "feed_errors": dict(self._feed_errors),
                   "age_s": (time.time() - self._last_poll_at) if self._last_poll_at else None}
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED, details=details)
        if self._polls == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA, details=details)
        if self._feeds and len(self._feed_errors) == len(self._feeds):
            return HealthStatus(state=HealthState.DEGRADED, message="ALL_FEEDS_UNAVAILABLE", details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="polls=%d published=%d" % (self._polls, self._published),
                            details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "polls": self._polls, "published": self._published}

    async def restore(self, state: dict[str, Any]) -> None:
        if isinstance(state, dict):
            self._polls = int(state.get("polls", 0))
            self._published = int(state.get("published", 0))
