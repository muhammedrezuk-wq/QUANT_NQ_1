"""حماية البوت من الرشق — ختم المالك ٢٠٢٦-٠٨-٢٠.

المطلوب حرفيًّا: «بجوز حدا يطلب ٣٠ متكرر، أو ١٠ أشخاص مع بعض — ما يسقط،
ينفّذ طلبًا واحدًا لكل محادثة بكل دفعة».

ثلاث طبقات، بلا أي مكتبة جديدة:

  ① قفل لكل محادثة (single-flight): طلب واحد ينفَّذ لكل محادثة في اللحظة.
     الكبسات التالية تُرمى، ويصل سطر تنبيه **مرّة واحدة** لا مع كل كبسة.
  ② مهلة تهدئة: مسافة دنيا بين طلبين لنفس المحادثة تبتلع الرشق السريع.
  ③ سقف تنفيذ عام للعمليات الثقيلة (شبكة/ترجمة) كي لا يخنق عشرة أشخاص البوت.

لا شيء هنا يكذب على المستخدم: الطلب المرميّ يُعلَن مرميًّا، ولا يُنفَّذ
لاحقًا بصمت ولا يُحسب منفَّذًا.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Awaitable, Callable, Dict, Optional

from telegram import Update
from telegram.ext import ContextTypes

from app.state_manager import StateManager

logger = logging.getLogger("QUANT_NQ_NEWS")

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

# القيم مقصودة ومحافظة — تُعدَّل من مكان واحد
COOLDOWN_SECONDS = 2.0        # مسافة دنيا بين طلبين لنفس المحادثة
HEAVY_CONCURRENCY = 4         # أقصى عمليات ثقيلة متزامنة (شبكة/ترجمة)
NOTICE_EVERY_SECONDS = 10.0   # لا نكرّر تنبيه «قيد التنفيذ» أكثر من مرّة كل ١٠ث


class Throttle:
    def __init__(self, state: Optional[StateManager] = None) -> None:
        # الحالة اختيارية كي تبقى الوحدة قابلة للاختبار وحدها.
        self.state = state
        self._busy: Dict[int, bool] = {}
        self._last_run: Dict[int, float] = {}
        self._last_notice: Dict[int, float] = {}
        self._heavy = asyncio.Semaphore(HEAVY_CONCURRENCY)
        self.dropped = 0
        self.served = 0

    def _record_error(self, chat_id: int, exc: BaseException) -> None:
        """يكتب الخطأ في الحالة كي تقرأه لوحة `/health`.

        كانت اللوحة تعرض «آخر خطأ: لا يوجد» **دائمًا** لأنّ لا أحد في المسار
        الحيّ يكتب `last_error` — والسجلّ فيه مئات أسطر ERROR. لوحة صحّة
        تطمئن بلا أن تفحص شيئًا أسوأ من لا لوحة.
        """
        if self.state is None:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.state.set(
                "last_error",
                "%s | أمر من المحادثة %s: %s: %s"
                % (stamp, chat_id, type(exc).__name__, exc),
            )
        except Exception:
            pass

    async def _notice(self, update: Update, text: str, chat_id: int) -> None:
        """تنبيه واحد لكل نافذة — كي لا نردّ ٣٠ مرّة على ٣٠ كبسة."""
        now = time.monotonic()
        if now - self._last_notice.get(chat_id, 0.0) < NOTICE_EVERY_SECONDS:
            return
        self._last_notice[chat_id] = now
        try:
            if update.message:
                await update.message.reply_text(text)
        except Exception:
            pass

    def wrap(self, handler: Handler, heavy: bool = False) -> Handler:
        async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            chat = update.effective_chat
            chat_id = chat.id if chat else 0
            now = time.monotonic()

            # ① طلب سابق لنفس المحادثة لسّا شغّال
            if self._busy.get(chat_id):
                self.dropped += 1
                await self._notice(update, "🕐 طلبك السابق لسّا قيد التنفيذ — استنى ثانية.", chat_id)
                return

            # ② تهدئة: رشق سريع من نفس المحادثة
            if now - self._last_run.get(chat_id, 0.0) < COOLDOWN_SECONDS:
                self.dropped += 1
                await self._notice(update, "🕐 على مهلك — طلب واحد كل ثانيتين.", chat_id)
                return

            self._busy[chat_id] = True
            try:
                if heavy:
                    # ③ سقف عام: العاشر ينتظر دوره بدل خنق البوت
                    async with self._heavy:
                        await handler(update, context)
                else:
                    await handler(update, context)
                self.served += 1
            except Exception as exc:
                # سقوط أمر واحد لا يُسقط البوت، والخطأ يُسجَّل لا يُبتلع صامتًا
                logger.exception("فشل تنفيذ أمر من المحادثة %s", chat_id)
                self._record_error(chat_id, exc)
                try:
                    if update.message:
                        await update.message.reply_text("صار خلل بتنفيذ طلبك. جرّب بعد شوي.")
                except Exception:
                    pass
            finally:
                self._busy[chat_id] = False
                self._last_run[chat_id] = time.monotonic()

        return guarded
