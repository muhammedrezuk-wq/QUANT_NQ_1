from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


@dataclass
class Config:
    bot_token: str
    owner_chat_id: int
    timezone: str
    morning_report_hour: int
    morning_report_minute: int
    data_dir: str
    log_dir: str
    nq_brain_path: str



def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    owner_chat_id = int(os.getenv("OWNER_CHAT_ID", "0").strip() or 0)
    timezone = os.getenv("TIMEZONE", "Europe/Istanbul").strip()
    morning_report_hour = int(os.getenv("MORNING_REPORT_HOUR", "14"))
    morning_report_minute = int(os.getenv("MORNING_REPORT_MINUTE", "0"))
    data_dir = os.getenv("DATA_DIR", "data").strip()
    log_dir = os.getenv("LOG_DIR", "logs").strip()
    nq_brain_path = os.getenv("NQ_BRAIN_PATH", "").strip()

    return Config(
        bot_token=bot_token,
        owner_chat_id=owner_chat_id,
        timezone=timezone,
        morning_report_hour=morning_report_hour,
        morning_report_minute=morning_report_minute,
        data_dir=data_dir,
        log_dir=log_dir,
        nq_brain_path=nq_brain_path,
    )
