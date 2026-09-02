from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # بيئة قديمة — نبقى على UTC بدل الكذب بتوقيت غير مضبوط
    ZoneInfo = None  # type: ignore

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.analysis_service import AnalysisService
from app.calendar_bridge import CalendarBridge
from app.macro_bridge import MacroBridge
from app.news_service import NewsService, symbols_in
from app.reporting import build_morning_report
from app.routing_service import RoutingService
from app.state_manager import StateManager
from app.storage import JsonStorage
from app.throttle import Throttle
from app.translator import TranslatorService


logger = logging.getLogger("QUANT_NQ_NEWS")


class TelegramBot:
    def __init__(self, application: Application, storage: JsonStorage, state: StateManager):
        self.application = application
        self.storage = storage
        self.state = state
        self.news_service = NewsService()
        self.analysis_service = AnalysisService()
        # الأجندة صارت من تقويم ميتاتريدر عبر قاعدة الجسر — بلا مفتاح API.
        self.calendar_service = CalendarBridge()
        self.routing_service = RoutingService()
        self.translator = TranslatorService()
        self.macro_bridge = MacroBridge()
        # الحارس يمسك الحالة كي يُكتب كل فشل في `last_error` بدل ضياعه.
        self.throttle = Throttle(state)

    def main_keyboard(self, is_owner: bool = False) -> ReplyKeyboardMarkup:
        rows = [
            ["📊 التقرير الصباحي", "📰 الأخبار"],
            ["📅 الأجندة", "⚙️ الحالة"],
            ["🩺 الصحة", "🆔 معرفي"],
            ["📘 المساعدة"],
        ]
        if is_owner:
            rows.append(["👥 المستخدمون"])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True)

    def register_handlers(self) -> None:
        # كل أمر يمرّ بحارس الرشق: طلب واحد لكل محادثة، وسقف للعمليات الثقيلة.
        g = self.throttle.wrap
        self.application.add_handler(CommandHandler("start", g(self.start)))
        self.application.add_handler(CommandHandler("help", g(self.help_command)))
        self.application.add_handler(CommandHandler("morning", g(self.morning_report, heavy=True)))
        self.application.add_handler(CommandHandler("news", g(self.news, heavy=True)))
        self.application.add_handler(CommandHandler("calendar", g(self.calendar)))
        self.application.add_handler(CommandHandler("status", g(self.status)))
        self.application.add_handler(CommandHandler("health", g(self.health)))
        self.application.add_handler(CommandHandler("id", g(self.my_id)))
        self.application.add_handler(CommandHandler("add_user", g(self.add_user)))
        self.application.add_handler(CommandHandler("remove_user", g(self.remove_user)))
        self.application.add_handler(CommandHandler("list_users", g(self.list_users)))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, g(self.text_router, heavy=True)))

    async def _notify_owner_new_request(self, update: Update) -> None:
        owner_data = self.storage.get_users()
        owner_chat_id = int(owner_data.get("owner_chat_id", 0) or 0)
        if not owner_chat_id:
            return
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        username = f"@{user.username}" if user.username else "لا يوجد"
        full_name = user.full_name if user.full_name else "غير معروف"
        chat_id = chat.id
        text = (
            "🔔 طلب انضمام جديد للبوت\n\n"
            f"- الاسم: {full_name}\n"
            f"- اليوزر: {username}\n"
            f"- chat_id: {chat_id}\n\n"
            f"لإضافة هذا المستخدم:\n/add_user {chat_id}"
        )
        await self.application.bot.send_message(chat_id=owner_chat_id, text=text)

    async def _ensure_allowed(self, update: Update) -> bool:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        if self.storage.is_allowed(chat_id):
            return True
        if update.message:
            await update.message.reply_text("عذرًا، أنت غير مضاف حاليًا. أرسل /start لطلب الانضمام.")
        return False

    async def _ensure_owner(self, update: Update) -> bool:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        if self.storage.is_owner(chat_id):
            return True
        if update.message:
            await update.message.reply_text("هذا الأمر متاح للمالك فقط.")
        return False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        logger.info("/start from chat_id=%s", chat_id)
        if self.storage.is_allowed(chat_id):
            await update.message.reply_text(
                "أهلًا بك في QUANT_NQ_NEWS\n"
                "بوت عربي لمتابعة السوق الأمريكي والمؤشّرات والذهب والعملات:\n"
                "ناسداك · S&P · داو · ذهب · بيتكوين · يورو · إسترليني\n"
                "ومعها الأجندة الاقتصادية بوقتها.",
                reply_markup=self.main_keyboard(self.storage.is_owner(chat_id)),
            )
        else:
            await update.message.reply_text(
                f"تم استلام طلبك ✅\nمعرّفك هو: {chat_id}\nسيصل طلبك إلى المالك."
            )
            await self._notify_owner_new_request(update)

    async def text_router(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        text = update.message.text.strip()
        mapping = {
            "📊 التقرير الصباحي": self.morning_report,
            "📰 الأخبار": self.news,
            "📅 الأجندة": self.calendar,
            "⚙️ الحالة": self.status,
            "🩺 الصحة": self.health,
            "🆔 معرفي": self.my_id,
            "📘 المساعدة": self.help_command,
            "👥 المستخدمون": self.list_users,
        }
        handler = mapping.get(text)
        if handler:
            await handler(update, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_allowed(update):
            return
        await update.message.reply_text(
            "📘 الأوامر:\n"
            "/start\n/help\n/morning\n/news\n/calendar\n/status\n/health\n/id\n\n"
            "الإدارة:\n/add_user <chat_id>\n/remove_user <chat_id>\n/list_users",
            reply_markup=self.main_keyboard(self.storage.is_owner(update.effective_chat.id if update.effective_chat else 0)),
        )

    async def morning_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_allowed(update):
            return
        # البناء يسحب سبع تغذيات ويترجم — نداءات شبكة حاجزة. تنفيذها في
        # الحلقة كان يجمّد كل المستخدمين حتى ينتهي، فصارت بخيط منفصل.
        report = await asyncio.to_thread(build_morning_report)
        self.state.touch("last_morning_report")
        await update.message.reply_text(report)

    # أسماء المصادر ووسوم الأثر بالعربي — لا معرّفات خام في رسالة المستخدم.
    _SOURCE_AR = {
        "federal_reserve": "الاحتياطي الفيدرالي", "bea": "مكتب التحليل الاقتصادي",
        "cnbc_top": "CNBC — الأهمّ", "cnbc_markets": "CNBC — الأسواق",
        "marketwatch": "ماركت ووتش", "investing": "إنفستنغ",
        "yahoo_ndx": "ياهو — ناسداك", "yahoo_rss": "ياهو",
    }
    _IMPACT_AR = {"HIGH": "أثر عالٍ", "MEDIUM": "أثر متوسّط", "LOW": "أثر منخفض"}
    _IMPACT_ICON = {"HIGH": "🔴", "MEDIUM": "🔸", "LOW": "▫️"}
    NEWS_BATCH = 6          # كم خبرًا يُرسَل بالطلب الواحد

    def _published_local(self, item: dict) -> str:
        """وقت نشر الخبر بتوقيت البوت — من التغذية، لا وقت السحب."""
        stamp = item.get("published_at")
        if not stamp:
            return ""
        try:
            moment = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return ""
        try:
            moment = moment.astimezone(ZoneInfo(os.getenv("TIMEZONE", "Europe/Istanbul").strip()))
        except Exception:
            pass
        return moment.strftime("%H:%M")

    def format_news_message(self, item: dict, next_event: str = "") -> str:
        """رسالة الخبر — أمر المالك ٢٠٢٦-٠٨-٢٥.

        ما تغيّر ولماذا:
          ① **الملخّص يظهر**. كان يُسحب من التغذية ويُخزَّن في قاعدة الجسر
             ولا يُعرض للقارئ أبدًا — فيخرج الطالب بعنوان بلا فائدة.
          ② **الرموز تُذكر**: من يخصّه الخبر (USTEC · XAUUSD…)، لا وسم عامّ.
          ③ **سطر «ما يُترقَّب»**: أقرب حدث في تقويم المنصّة ومعه المتوقّع
             والسابق. توقّع **منشور رسميًّا**، لا ترجيح نخترعه — والفرق بينهما
             هو الفرق بين خبر وإشاعة.
          ④ وقت النشر يظهر: «متى قيل هذا» جزء من الخبر لا زينة.
        """
        analyzed = self.analysis_service.analyze_news_item(item)
        source = self._SOURCE_AR.get(str(item.get("source") or ""), str(item.get("source") or "—"))
        level = str(item.get("impact_level") or "")
        symbols = symbols_in(str(item.get("title") or ""))

        head = ["%s %s" % (self._IMPACT_ICON.get(level, "▫️"),
                           self._IMPACT_AR.get(level, "خبر"))]
        if symbols:
            head.append(" · ".join(symbols))
        lines = [" · ".join(head), ""]
        lines.append("📌 %s" % analyzed.get("title_ar", analyzed.get("title", "")))

        summary = str(item.get("summary_ar") or "").strip()
        if summary:
            lines += ["", "📝 %s" % summary[:400]]

        lines.append("")
        if next_event:
            lines.append("📅 ما يُترقَّب: %s" % next_event)
        lines.append("📊 قراءة الكلمات: %s" % analyzed.get("impact", "غير محدد"))
        # الغياب يُعلَن ولا يُخفى: حذف الوقت صمتًا يجعل القارئ يفترض أنّه «الآن».
        # (وهذا ما يفعله المستهلك الخارجي فعلًا حين يجد الحقل فارغًا.)
        moment = self._published_local(item) or "وقت النشر غير معلن من المصدر"
        lines.append("📰 %s · %s · %s"
                     % (source, analyzed.get("credibility", ""), moment))
        link = analyzed.get("link", "")
        if link:
            lines.append("🔗 %s" % link)
        return "\n".join(lines)

    async def news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_allowed(update):
            return
        raw_news = await asyncio.to_thread(self.news_service.fetch_latest_news, 20)
        self.state.touch("last_news_fetch")
        if not raw_news:
            await update.message.reply_text("لا توجد أخبار متاحة حاليًا.")
            return

        # ① نفرز أوّلًا ونقصّ على المُرسَل فعلًا، ② ثمّ نترجم كل خبر عند إرساله.
        # كان العكس: تُترجم العشرون ثمّ يُرسَل خمسة — فتُهدر خمس عشرة ترجمة
        # ويصل أوّل ردّ بعد ~١٧ ثانية. الآن أوّل ردّ يصل فور جهوزه.
        picked = [i for i in raw_news
                  if self.routing_service.get_routing_decision(i).get("send_to_telegram")]
        picked = picked[:self.NEWS_BATCH]
        if not picked:
            await update.message.reply_text("لا توجد أخبار تستحق الإرسال حاليًا.")
            return

        # سطر «ما يُترقَّب» يُحسب مرّة واحدة للطلب كلّه — لا استعلام لكل خبر.
        next_event = await asyncio.to_thread(self.calendar_service.next_event_line)

        for item in picked:
            ready = dict(item)
            # الترجمة نداء شبكة حاجز — تُنفَّذ بخيط كي لا تُجمّد بقيّة المستخدمين.
            # والملخّص يُترجَم الآن أيضًا: كان يُسحب ولا يُعرض، فيخرج القارئ
            # بعنوان بلا فائدة. الذاكرة على القرص تمنع تكرار كلفة الترجمة.
            ready["title_ar"] = await asyncio.to_thread(
                self.translator.translate_text, str(item.get("title") or ""))
            summary = str(item.get("summary") or "").strip()
            if summary:
                ready["summary_ar"] = await asyncio.to_thread(
                    self.translator.translate_text, summary[:300])
            await update.message.reply_text(self.format_news_message(ready, next_event))
            self.state.touch("last_news_sent")

    async def calendar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_allowed(update):
            return
        await update.message.reply_text(self.calendar_service.build_calendar_report())

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_allowed(update):
            return
        # نداء واحد لا نداءان: `is_event_lock_active()` يستدعي هذا نفسه.
        active_event = self.calendar_service.get_active_lock_event()
        if active_event:
            # جسر الأجندة يسمّي الحدث `title`؛ و`event` كان مفتاح خدمة Finnhub
            # القديمة. قراءته مباشرةً كانت ترفع KeyError فيسقط الأمر كلّه في
            # اللحظة التي وُجد لأجلها بالضبط، ويصل للمستخدم «صار خلل».
            name = (active_event.get("title") or active_event.get("event")
                    or "حدث اقتصادي قوي")
            when = str(active_event.get("time") or "").strip()
            suffix = f" — {when}" if when else ""
            await update.message.reply_text(
                f"البوت يعمل ✅\nEvent Lock مفعل بسبب: {name}{suffix}")
        else:
            await update.message.reply_text("البوت يعمل بشكل طبيعي ✅\nلا يوجد Event Lock الآن.")

    async def health(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_allowed(update):
            return
        self.state.set("bot_running", True)
        self.state.set("scheduler_running", True)
        self.state.set("event_lock", self.calendar_service.is_event_lock_active())
        self.state.set("macro_bridge_available", self.macro_bridge.is_available())
        state = self.state.get_all()
        users_count = len(self.storage.list_users())
        msg = (
            "🩺 لوحة الصحة التشغيلية\n\n"
            f"- البوت: {'يعمل ✅' if state.get('bot_running') else 'متوقف ❌'}\n"
            f"- المجدول: {'يعمل ✅' if state.get('scheduler_running') else 'متوقف ❌'}\n"
            f"- Event Lock: {'مفعل' if state.get('event_lock') else 'غير مفعل'}\n"
            f"- Macro Bridge: {'متاح' if state.get('macro_bridge_available') else 'غير متاح'}\n"
            f"- آخر جلب أخبار: {state.get('last_news_fetch') or 'لا يوجد'}\n"
            f"- آخر خبر مرسل: {state.get('last_news_sent') or 'لا يوجد'}\n"
            f"- آخر تقرير صباحي: {state.get('last_morning_report') or 'لا يوجد'}\n"
            f"- آخر نبضة: {state.get('last_heartbeat') or 'لا يوجد'}\n"
            f"- آخر خطأ: {state.get('last_error') or 'لا يوجد'}\n"
            f"- عدد المستخدمين: {users_count}"
        )
        await update.message.reply_text(msg)

    async def my_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else 0
        await update.message.reply_text(f"معرّفك هو: {chat_id}")

    async def add_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_owner(update):
            return
        if not context.args:
            await update.message.reply_text("استخدم الأمر هكذا:\n/add_user 123456789")
            return
        try:
            chat_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("chat_id غير صالح.")
            return
        added = self.storage.add_user(chat_id)
        if added:
            await update.message.reply_text(f"تمت إضافة المستخدم {chat_id} ✅")
            try:
                await self.application.bot.send_message(chat_id=chat_id, text="تمت الموافقة على انضمامك ✅")
            except Exception:
                pass
        else:
            await update.message.reply_text("المستخدم موجود بالفعل أو تعذر إضافته.")

    async def remove_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_owner(update):
            return
        if not context.args:
            await update.message.reply_text("استخدم الأمر هكذا:\n/remove_user 123456789")
            return
        try:
            chat_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("chat_id غير صالح.")
            return
        removed = self.storage.remove_user(chat_id)
        if removed:
            await update.message.reply_text(f"تم حذف المستخدم {chat_id} ✅")
        else:
            await update.message.reply_text("تعذر حذف المستخدم. ربما غير موجود أو أنه المالك.")

    async def list_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_owner(update):
            return
        users = self.storage.list_users()
        text = "\n".join(str(u) for u in users) if users else "لا يوجد مستخدمون."
        await update.message.reply_text(f"📋 قائمة المستخدمين:\n{text}")
