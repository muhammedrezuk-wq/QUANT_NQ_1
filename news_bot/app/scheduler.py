from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from app.calendar_bridge import CalendarBridge
from app.jsonio import write_json_atomic
from app.reporting import build_morning_report
from app.state_manager import StateManager
from app.storage import JsonStorage

# ── النشر التلقائي للأخبار ──────────────────────────────────────────
# أمر المالك ٢٠٢٦-٠٨-٢٥: «عدد أخبار منشورة، ووقت دقيق» — البوت كان لا ينشر
# خبرًا من نفسه إطلاقًا، ينتظر أن يضغط الطالب /news. الآن ينشر بوقته.
#
# مطفأ افتراضيًّا (`NEWS_PUSH=off`): تشغيله يرسل إلى كل مستخدم، وهذا قرار
# المالك لا قرار الكود. ويُضبط بثلاثة مفاتيح في .env:
#   NEWS_PUSH=on|off · NEWS_PUSH_MIN_IMPACT=HIGH|MEDIUM · NEWS_PUSH_MAX_PER_RUN=3
#   NEWS_PUSH_MAX_AGE_MIN=120  (عمر أقصى للخبر المنشور تلقائيًّا)
_IMPACT_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def is_fresh(item: dict, now: float, max_age_s: float) -> bool:
    """هل يستحقّ هذا الخبر النشر **الآن**؟

    قياس المالك ٢٠٢٦-٠٨-٢٥ على الطرف المستهلك: خبر بلا وقت نشر كان يُختم
    بلحظة سحبه فيُقرأ حدثًا وقع للتوّ — ووُجد خبر عمره ثلاثة أسابيع مختومًا
    بهذه اللحظة. الدرس ينطبق علينا حرفيًّا: النشر التلقائي يقول للطالب
    «هذا يحدث الآن»، فلا يجوز أن يقوله عن خبر لا نعرف وقته أو مضى عليه يوم.

    - بلا وقت نشر ⇒ **لا يُنشَر تلقائيًّا** (يبقى متاحًا بأمر /news موسومًا
      «وقت النشر غير معلن»، فالقارئ هناك يطلبه ويرى الوسم).
    - أقدم من السقف ⇒ لا يُنشَر: خبر أمس ليس عاجلًا مهما علا أثره.
    """
    stamp = item.get("published_at")
    if not stamp:
        return False
    try:
        age = now - float(stamp)
    except (TypeError, ValueError):
        return False
    return -300 <= age <= max_age_s        # خمس دقائق سماحًا لفارق ساعة المصدر


def _push_settings() -> tuple:
    """تُقرأ عند كل دورة لا عند الاستيراد: تغيير .env لا يحتاج تعديل كود."""
    enabled = os.getenv("NEWS_PUSH", "off").strip().lower() in ("1", "on", "true", "yes")
    min_impact = os.getenv("NEWS_PUSH_MIN_IMPACT", "HIGH").strip().upper()
    try:
        cap = max(1, int(os.getenv("NEWS_PUSH_MAX_PER_RUN", "3")))
    except ValueError:
        cap = 3
    try:
        max_age_s = max(60, int(os.getenv("NEWS_PUSH_MAX_AGE_MIN", "120"))) * 60
    except ValueError:
        max_age_s = 120 * 60
    return enabled, min_impact, cap, max_age_s


def _sent_ledger_path() -> str:
    return os.path.join(os.getenv("DATA_DIR", "data").strip() or "data", "news_sent.json")


def _load_sent() -> dict:
    try:
        import json
        with open(_sent_ledger_path(), "r", encoding="utf-8") as fh:
            return {str(k): float(v) for k, v in json.load(fh).items()}
    except Exception:
        return {}


def _remember_sent(sent: dict) -> None:
    cutoff = time.time() - 3 * 86400
    pruned = {k: v for k, v in sent.items() if v >= cutoff}
    try:
        write_json_atomic(_sent_ledger_path(), pruned)
    except OSError:
        pass


def news_key(title: str) -> str:
    """مفتاح ثابت للخبر: العنوان مُطبَّعًا ومُلخَّصًا — فلا يُنشر مرّتين."""
    return hashlib.sha256(" ".join(str(title or "").lower().split()).encode("utf-8")).hexdigest()[:16]


logger = logging.getLogger("QUANT_NQ_NEWS")


def record_error(state: StateManager, where: str, exc: BaseException) -> None:
    """يكتب الخطأ في الحالة كي تقرأه لوحة `/health`.

    كانت اللوحة تعرض «آخر خطأ: لا يوجد» **دائمًا** لأنّ أحدًا في المسار الحيّ
    لا يكتب `last_error` إطلاقًا — بينما السجلّ فيه مئات أسطر ERROR. لوحة
    صحّة تقول «سليم» بلا أن تفحص شيئًا أسوأ من لا لوحة.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.warning("فشل %s: %s: %s", where, type(exc).__name__, exc)
    try:
        state.set("last_error", "%s | %s: %s: %s" % (stamp, where, type(exc).__name__, exc))
    except Exception:
        pass


async def send_morning_report(application: Application, storage: JsonStorage, state: StateManager) -> None:
    # بناء التقرير يسحب سبع تغذيات ويترجم ستّة عناوين — كلّها نداءات شبكة
    # حاجزة. تنفيذها داخل حلقة البوت كان يجمّد كل المستخدمين ثوانيَ عدّة.
    try:
        message = await asyncio.to_thread(build_morning_report)
    except Exception as exc:
        record_error(state, "بناء التقرير الصباحي", exc)
        return

    delivered = 0
    for chat_id in storage.list_users():
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
            delivered += 1
        except Exception as exc:
            record_error(state, "إرسال التقرير الصباحي إلى %s" % chat_id, exc)
    if delivered:
        state.touch("last_morning_report")


async def announce_calendar(application: Application, storage: JsonStorage, state: StateManager) -> None:
    """النشر التلقائي لأحداث الأجندة بوقتها — تنبيه قبل الحدث ونتيجة عند صدورها.

    يُنادى كل دقيقة. المفتاح يُختم «مُرسَلًا» **بعد** وصول الرسالة فعلًا، فإن
    سقطت الشبكة تُعاد المحاولة بالدقيقة التالية بدل أن يضيع التنبيه للأبد.
    وإن غابت قاعدة الجسر لا يُرسَل شيء ولا يُخترع.
    """
    bridge = CalendarBridge()
    due = bridge.due_announcements()
    if not due:
        return
    users = storage.list_users()
    if not users:
        return

    delivered: list[str] = []
    for key, text in due:
        reached = 0
        for chat_id in users:
            try:
                await application.bot.send_message(chat_id=chat_id, text=text)
                reached += 1
            except Exception as exc:
                record_error(state, "إرسال الأجندة إلى %s" % chat_id, exc)
        # وصلت أحدًا ⇒ تُختم. لم تصل أحدًا ⇒ تبقى مستحقّة للدقيقة التالية.
        if reached:
            delivered.append(key)

    if delivered:
        bridge.mark_sent(delivered)
        state.touch("last_calendar_push")


async def pull_news(state: StateManager, application=None, storage: JsonStorage = None,
                    bot=None) -> None:
    """سحب الأخبار وكتابتها بقاعدة الجسر — كل عشر دقائق.

    كان السحب يدويًّا بتشغيل `bridge_writer.py` باليد، فتبقى الأخبار قديمة
    وتظهر اللوحة عشرين خبرًا لا تتغيّر. الآن يجري تلقائيًّا مع البوت.
    الكتابة في خيط منفصل: `feedparser` و`sqlite` نداءان حاجزان، وحلقة
    البوت لا يجوز أن تُحجَز.
    """
    import sqlite3
    import time as _time

    def work() -> tuple:
        from app.news_service import NewsService
        import bridge_writer as bw
        service = NewsService()
        items = service.fetch_latest_news(30)
        con = sqlite3.connect(bw._db_path(), timeout=5.0)
        try:
            con.execute("PRAGMA busy_timeout=3000")
            con.execute(bw._NEWS_TABLE)
            bw.ensure_columns(con)
            new, updated = bw.write_news(con, items, _time.time())
        finally:
            con.close()
        return ("جديد %d · مُكمَّل %d · محفوظ %s" % (new, updated, service.last_stats),
                items)

    try:
        result, items = await asyncio.to_thread(work)
        state.set("last_news_pull", result)
    except Exception as exc:
        state.set("last_news_pull", "فشل السحب")
        record_error(state, "سحب الأخبار", exc)
        return

    # السحب والنشر في دورة واحدة: لا نداء شبكة ثانٍ لنفس الأخبار.
    if application is not None and storage is not None and bot is not None:
        await push_news(application, storage, state, bot, items)


async def push_news(application, storage: JsonStorage, state: StateManager,
                    bot, items: list) -> None:
    """ينشر الأخبار المستحقّة الآن — بترتيب الأثر، وبلا تكرار أبدًا.

    الخبر يُختم «منشورًا» بعد وصوله أحدًا فعلًا، كرسائل الأجندة تمامًا: سقوط
    الشبكة يعني إعادة المحاولة بالدورة التالية لا ضياع الخبر.
    """
    enabled, min_impact, cap, max_age_s = _push_settings()
    if not enabled or not items:
        return
    users = storage.list_users()
    if not users:
        return

    threshold = _IMPACT_RANK.get(min_impact, 3)
    now = time.time()
    candidates = [i for i in items
                  if _IMPACT_RANK.get(str(i.get("impact_level") or ""), 0) >= threshold
                  and is_fresh(i, now, max_age_s)]
    if not candidates:
        return

    sent = _load_sent()
    fresh = [i for i in candidates if news_key(i.get("title")) not in sent][:cap]
    if not fresh:
        return

    next_event = await asyncio.to_thread(CalendarBridge().next_event_line)
    delivered = []
    for item in fresh:
        ready = dict(item)
        try:
            ready["title_ar"] = await asyncio.to_thread(
                bot.translator.translate_text, str(item.get("title") or ""))
            summary = str(item.get("summary") or "").strip()
            if summary:
                ready["summary_ar"] = await asyncio.to_thread(
                    bot.translator.translate_text, summary[:300])
            text = bot.format_news_message(ready, next_event)
        except Exception as exc:
            record_error(state, "تجهيز خبر للنشر", exc)
            continue

        reached = 0
        for chat_id in users:
            try:
                await application.bot.send_message(chat_id=chat_id, text=text)
                reached += 1
            except Exception as exc:
                record_error(state, "نشر خبر إلى %s" % chat_id, exc)
        if reached:
            delivered.append(news_key(item.get("title")))

    if delivered:
        for key in delivered:
            sent[key] = now
        _remember_sent(sent)
        state.set("last_news_push", "%s · %d خبرًا"
                  % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(delivered)))


async def heartbeat(state: StateManager) -> None:
    state.touch("last_heartbeat")


def build_scheduler(
    application: Application,
    storage: JsonStorage,
    state: StateManager,
    timezone: str,
    hour: int,
    minute: int,
    bot=None,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        send_morning_report,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        args=[application, storage, state],
        id="morning_report",
        replace_existing=True,
    )
    scheduler.add_job(
        heartbeat,
        CronTrigger(minute="*/5", timezone=timezone),
        args=[state],
        id="heartbeat",
        replace_existing=True,
    )
    # نبضة الأجندة: كل دقيقة — لأنّ «بوقتها» تعني بالدقيقة لا بالساعة.
    scheduler.add_job(
        announce_calendar,
        CronTrigger(minute="*", timezone=timezone),
        args=[application, storage, state],
        id="calendar_push",
        replace_existing=True,
    )
    # سحب الأخبار وكتابتها بقاعدة الجسر — تغذّي البوت واللوحة معًا.
    scheduler.add_job(
        pull_news,
        CronTrigger(minute="*/10", timezone=timezone),
        args=[state, application, storage, bot],
        id="news_pull",
        replace_existing=True,
    )
    return scheduler
