"""جسر الأجندة الاقتصادية — من تقويم ميتاتريدر المدمج إلى بوت تلغرام.

المصدر: جدول `calendar` داخل قاعدة الجسر التي يكتبها الإكسبرت
(`WriteCalendar()` عبر `CalendarValueHistory` / `CalendarEventById`).
الأعمدة الحقيقية المقيسة: id · title · country · currency · impact_level ·
scheduled_at (unix) · actual · forecast · previous · written_at.

لماذا هذا المصدر لا Finnhub: العناوين تصل **مترجمة عربيًّا** من المنصّة،
وفيها العملة والأهمية والمتوقّع والسابق والفعلي — **بلا مفتاح API ولا اشتراك**.

القراءة **للقراءة فقط** (`mode=ro`): هذا الملف لا يكتب في قاعدة الجسر إطلاقًا،
ولا يلمس مشروع QUANT_NQ ولا أي ذرّة فيه.

الواجهة مطابقة لـ`CalendarService` القديم (بديل مباشر)، مع إضافة
`due_announcements()` للنشر التلقائي بوقته.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

from app.jsonio import write_json_atomic
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # بيئة قديمة — نبقى على UTC بدل الكذب بتوقيت غير مضبوط
    ZoneInfo = None  # type: ignore

_DEFAULT_DB = os.path.join(
    os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal", "Common", "Files", "nq_brain.db"
)

_IMPACT_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
_IMPACT_AR = {"NONE": "بلا أثر", "LOW": "منخفضة", "MEDIUM": "متوسطة", "HIGH": "عالية"}
_IMPACT_ICON = {"NONE": "▫️", "LOW": "🔹", "MEDIUM": "🔸", "HIGH": "🔴"}


def _rank(level: str) -> int:
    return _IMPACT_RANK.get(str(level or "").upper(), 0)


def minutes_ar(n: int) -> str:
    """صيغة عربية سليمة للدقائق — الرسالة تصل لطلّاب، فالركاكة تُقرأ إهمالًا."""
    if n == 1:
        return "دقيقة واحدة"
    if n == 2:
        return "دقيقتين"
    if 3 <= n <= 10:
        return "%d دقائق" % n
    return "%d دقيقة" % n


def _num(value: str) -> Optional[float]:
    """يقرأ رقمًا من نصّ الحقل (قد يحمل % أو K أو فاصلة) — أو None."""
    if not value:
        return None
    cleaned = str(value).strip().replace(",", "").replace("%", "")
    mult = 1.0
    if cleaned and cleaned[-1] in "KkMmBb":
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}[cleaned[-1].lower()]
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * mult
    except ValueError:
        return None


def compare_actual(actual: str, forecast: str) -> str:
    """الفرق بين الفعلي والمتوقّع — **وصفًا رقميًّا فقط**.

    ⛔ عمدًا لا نقول «إيجابي للدولار» ولا «سلبي»: معنى الاتجاه يختلف باختلاف
    المؤشّر (ارتفاع مطالبات البطالة سيّئ، وارتفاع الناتج جيّد). الحكم بالاتجاه
    يحتاج معرفة لكل مؤشّر على حدة — وما لا نملكه لا نخترعه.
    """
    a, f = _num(actual), _num(forecast)
    if a is None or f is None:
        return ""
    diff = a - f
    if abs(diff) < 1e-9:
        return "مطابق للمتوقّع تمامًا"
    fmt = ("%.4f" % abs(diff)).rstrip("0").rstrip(".")
    return ("أعلى من المتوقّع بـ%s" if diff > 0 else "أقلّ من المتوقّع بـ%s") % fmt


class CalendarBridge:
    def __init__(self, data_dir: str = "data") -> None:
        self.db_path = (os.getenv("NQ_BRIDGE_DB", "").strip() or _DEFAULT_DB)
        self.currency = os.getenv("CAL_CURRENCY", "USD").strip().upper()
        self.min_impact = os.getenv("CAL_MIN_IMPACT", "MEDIUM").strip().upper()
        self.alert_minutes = int(os.getenv("CAL_ALERT_MINUTES", "15") or 15)
        self.tz_name = os.getenv("TIMEZONE", "Europe/Istanbul").strip()
        self._sent_path = os.path.join(data_dir, "calendar_sent.json")

    # ── أدوات ──────────────────────────────────────────────────────────
    def available(self) -> bool:
        return bool(self.db_path) and os.path.isfile(self.db_path)

    def _tz(self):
        if ZoneInfo is None:
            return timezone.utc
        try:
            return ZoneInfo(self.tz_name)
        except Exception:
            return timezone.utc

    def _local(self, epoch: float) -> datetime:
        # الطابع يصل أحيانًا ناقصًا ثانيةً أو ثانيتين (15:29:59 بدل 15:30)،
        # وقصّ الثواني كان يعرض 15:29. التقريب لأقرب دقيقة يعرض وقت الحدث كما هو.
        rounded = round(float(epoch) / 60.0) * 60
        return datetime.fromtimestamp(rounded, tz=timezone.utc).astimezone(self._tz())

    def _query(self, sql: str, args: tuple = ()) -> List[sqlite3.Row]:
        if not self.available():
            return []
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            try:
                return list(con.execute(sql, args))
            finally:
                con.close()
        except sqlite3.Error:
            return []

    def _rows(self, start: float, end: float) -> List[Dict[str, str]]:
        rows = self._query(
            "SELECT id, title, country, currency, impact_level, scheduled_at,"
            " actual, forecast, previous FROM calendar"
            " WHERE scheduled_at >= ? AND scheduled_at < ?"
            " ORDER BY scheduled_at ASC",
            (start, end),
        )
        out: List[Dict[str, str]] = []
        for r in rows:
            cur = str(r["currency"] or "").upper()
            if self.currency and self.currency != "ALL" and cur != self.currency:
                continue
            if _rank(r["impact_level"]) < _rank(self.min_impact):
                continue
            out.append({
                "id": str(r["id"]),
                "title": str(r["title"] or "").strip(),
                "country": str(r["country"] or "").strip(),
                "currency": cur,
                "impact": str(r["impact_level"] or "").upper(),
                "epoch": float(r["scheduled_at"] or 0),
                "time": self._local(r["scheduled_at"] or 0).strftime("%H:%M"),
                "actual": str(r["actual"] or "").strip(),
                "forecast": str(r["forecast"] or "").strip(),
                "previous": str(r["previous"] or "").strip(),
            })
        return out

    # ── الواجهة المطابقة للخدمة القديمة ───────────────────────────────
    def get_today_events(self) -> List[Dict[str, str]]:
        now = datetime.now(self._tz())
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self._rows(day.timestamp(), (day + timedelta(days=1)).timestamp())

    def get_active_lock_event(self) -> Optional[Dict[str, str]]:
        now = datetime.now(timezone.utc).timestamp()
        window = self.alert_minutes * 60
        for e in self.get_today_events():
            if e["impact"] == "HIGH" and abs(now - e["epoch"]) <= window:
                return e
        return None

    def is_event_lock_active(self) -> bool:
        return self.get_active_lock_event() is not None

    def get_top_event_summary(self) -> str:
        events = self.get_today_events()
        if not events:
            if not self.available():
                return "قاعدة الجسر غير موجودة — ما في أجندة (شغّل الإكسبرت على المنصّة)."
            return f"ما في أحداث {self.currency} اليوم بالأهمية المطلوبة."
        high = [e for e in events if e["impact"] == "HIGH"]
        top = high[0] if high else events[0]
        return f"{top['time']} — {top['title']} (الأهمية: {_IMPACT_AR.get(top['impact'], top['impact'])})"

    def next_event(self) -> Optional[Dict[str, str]]:
        """أقرب حدث قادم خلال ٢٤ ساعة بالعملة والأهمية المضبوطتين."""
        now = datetime.now(timezone.utc).timestamp()
        upcoming = self._rows(now, now + 24 * 3600)
        return upcoming[0] if upcoming else None

    def next_event_line(self) -> str:
        """سطر «ما يُترقَّب» الذي يُرفَق بالخبر.

        أمر المالك ٢٠٢٦-٠٨-٢٥: القارئ يريد توقّعًا مع الخبر. والتوقّع هنا
        **منشور رسميًّا** في تقويم المنصّة (المتوقّع · السابق · الموعد) — لا
        ترجيح نخترعه. ما لا نملكه لا نقوله.
        """
        event = self.next_event()
        if not event:
            return ""
        line = "%s — %s" % (event["time"], event["title"])
        if event["forecast"] or event["previous"]:
            line += " · المتوقّع %s · السابق %s" % (
                event["forecast"] or "—", event["previous"] or "—")
        return line

    def _line(self, e: Dict[str, str]) -> str:
        icon = _IMPACT_ICON.get(e["impact"], "▫️")
        parts = [f"{icon} {e['time']} — {e['title']}"]
        extra = []
        if e["forecast"]:
            extra.append(f"المتوقّع {e['forecast']}")
        if e["previous"]:
            extra.append(f"السابق {e['previous']}")
        if e["actual"]:
            extra.append(f"الفعلي {e['actual']}")
        if extra:
            parts.append("     " + " · ".join(extra))
        return "\n".join(parts)

    def build_calendar_report(self) -> str:
        now = datetime.now(self._tz())
        head = f"📅 أجندة {self.currency} — {now.strftime('%Y-%m-%d')}"
        if not self.available():
            return f"{head}\n\n⚠️ قاعدة الجسر غير موجودة. شغّل الإكسبرت على المنصّة."
        events = self.get_today_events()
        if not events:
            return f"{head}\n\nما في أحداث بالأهمية المطلوبة اليوم."
        done = [e for e in events if e["actual"]]
        soon = [e for e in events if not e["actual"]]
        lines = [head, ""]
        if soon:
            lines.append("— القادم —")
            lines += [self._line(e) for e in soon]
        if done:
            lines.append("")
            lines.append("— صدر اليوم —")
            lines += [self._line(e) for e in done]
        lines.append("")
        lines.append(f"التوقيت: {self.tz_name} · الأهمية: {_IMPACT_AR.get(self.min_impact, self.min_impact)} فما فوق")
        return "\n".join(lines)

    # ── النشر التلقائي بوقته ──────────────────────────────────────────
    def _sent(self) -> Dict[str, float]:
        try:
            with open(self._sent_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return {str(k): float(v) for k, v in data.items()}
        except Exception:
            return {}

    def _remember(self, sent: Dict[str, float]) -> None:
        cutoff = datetime.now(timezone.utc).timestamp() - 3 * 86400
        pruned = {k: v for k, v in sent.items() if v >= cutoff}
        try:
            write_json_atomic(self._sent_path, pruned)
        except OSError:
            pass

    def mark_sent(self, keys: Iterable[str]) -> None:
        """يُسجَّل المفتاح **بعد** وصول الرسالة فعلًا — لا قبلها.

        كان `due_announcements()` يسجّل المفتاح ثمّ يُعيد النصّ، والمجدول
        يرسل بعدها داخل `try/except`. فإن سقطت الشبكة تلك اللحظة (وقد سقطت
        فعلًا: `getaddrinfo failed` بالسجلّ) يضيع تنبيه الحدث **للأبد**،
        لأنّه صار «مُرسَلًا» في الدفتر بلا أن يصل أحدًا.
        """
        keys = [str(k) for k in keys]
        if not keys:
            return
        now = datetime.now(timezone.utc).timestamp()
        sent = self._sent()
        for key in keys:
            sent[key] = now
        self._remember(sent)

    def due_announcements(self) -> List[Tuple[str, str]]:
        """الرسائل المستحقّة الآن: تنبيه قبل الحدث، ونتيجة عند صدورها.

        لا يُسجَّل شيء هنا: المُرسِل هو من ينادي `mark_sent()` بعد وصول
        الرسالة فعلًا. فما لم يصل يُعاد عرضه بالدقيقة التالية بدل أن يضيع.
        """
        if not self.available():
            return []
        now = datetime.now(timezone.utc).timestamp()
        sent = self._sent()
        out: List[Tuple[str, str]] = []

        # نافذة زمنية لا «يوم تقويمي»: حدث الساعة 00:05 كان تنبيهه المستحقّ
        # 23:50 من اليوم السابق — ويوم أمس لا يحويه ويوم اليوم لم يبدأ بعد،
        # فيصل التنبيه متأخّرًا عند منتصف الليل. النافذة تعبر منتصف الليل.
        lead = self.alert_minutes * 60
        events = self._rows(now - 6 * 3600, now + lead + 120)

        for e in events:
            # ① تنبيه قبل الحدث
            key_pre = f"pre:{e['id']}"
            if key_pre not in sent and 0 <= (e["epoch"] - now) <= lead:
                txt = (
                    f"⏰ بعد {minutes_ar(self.alert_minutes)} — {e['currency']}\n\n"
                    f"{_IMPACT_ICON.get(e['impact'], '▫️')} {e['title']}\n"
                    f"الوقت: {e['time']} ({self.tz_name})\n"
                    f"الأهمية: {_IMPACT_AR.get(e['impact'], e['impact'])}"
                )
                if e["forecast"] or e["previous"]:
                    txt += f"\nالمتوقّع: {e['forecast'] or '—'} · السابق: {e['previous'] or '—'}"
                out.append((key_pre, txt))

            # ② النتيجة عند صدورها
            key_act = f"act:{e['id']}"
            if key_act not in sent and e["actual"] and (now - e["epoch"]) <= 6 * 3600:
                verdict = compare_actual(e["actual"], e["forecast"])
                txt = (
                    f"📢 صدرت النتيجة — {e['currency']}\n\n"
                    f"{_IMPACT_ICON.get(e['impact'], '▫️')} {e['title']}\n"
                    f"الفعلي: {e['actual']}\n"
                    f"المتوقّع: {e['forecast'] or '—'} · السابق: {e['previous'] or '—'}"
                )
                if verdict:
                    txt += f"\n\n📐 {verdict}"
                out.append((key_act, txt))

        return out
