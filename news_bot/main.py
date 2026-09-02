from __future__ import annotations

import asyncio
import os

from telegram.ext import Application

from app.config import load_config
from app.logger import setup_logger
from app.scheduler import build_scheduler
from app.single_instance import acquire, release
from app.state_manager import StateManager
from app.storage import JsonStorage
from app.telegram_bot import TelegramBot


async def main() -> int:
    config = load_config()
    logger = setup_logger(config.log_dir)

    os.makedirs(config.data_dir, exist_ok=True)
    os.makedirs(config.log_dir, exist_ok=True)

    # نسخة واحدة فقط: نسختان بنفس التوكن تتقاطعان عند تلغرام (409 Conflict)
    # فلا يعمل البوت أصلًا. القفل يُحرَّر تلقائيًّا عند موت العملية.
    lock = acquire(os.path.join(config.data_dir, "bot.lock"))
    if lock is None:
        msg = (
            "\n  البوت شغّال أصلًا بنافذة ثانية.\n"
            "  سكّر النافذة القديمة، أو استعملها هي — نسخة وحدة بس بتشتغل.\n"
        )
        print(msg)
        logger.error("رُفض التشغيل: نسخة أخرى من البوت ماسكة القفل")
        return 2

    storage = JsonStorage(config.data_dir)
    storage.set_owner_if_empty(config.owner_chat_id)
    state = StateManager(config.data_dir)

    if not config.bot_token:
        raise ValueError("BOT_TOKEN غير موجود داخل ملف .env")
    if not config.owner_chat_id:
        logger.warning("OWNER_CHAT_ID غير مضبوط بعد. يجب ضبطه داخل .env")

    # معالجة متوازية محدودة: عشرة أشخاص مع بعض يُخدمون سوا، بلا طابور خانق
    # وبلا انفلات — والحدّ الأدق للعمليات الثقيلة داخل app/throttle.py.
    application = (
        Application.builder()
        .token(config.bot_token)
        .concurrent_updates(8)
        .build()
    )

    bot = TelegramBot(application, storage, state)
    bot.register_handlers()

    scheduler = build_scheduler(
        application=application,
        storage=storage,
        state=state,
        timezone=config.timezone,
        hour=config.morning_report_hour,
        minute=config.morning_report_minute,
        bot=bot,
    )
    scheduler.start()

    state.set("bot_running", True)
    state.set("scheduler_running", True)
    logger.info("QUANT_NQ_NEWS started successfully")

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        state.set("bot_running", False)
        state.set("scheduler_running", False)
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        scheduler.shutdown()
        release(lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
