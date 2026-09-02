from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


logger = logging.getLogger("QUANT_NQ_NEWS")

db_manager = None

try:
    nq_brain_path_str = os.getenv("NQ_BRAIN_PATH", "").strip()
    if nq_brain_path_str:
        nq_brain_path = Path(nq_brain_path_str)
        if nq_brain_path.exists():
            sys.path.append(str(nq_brain_path))
            from modules.database_manager import DatabaseManager  # type: ignore

            db_manager = DatabaseManager(
                db_path=str(nq_brain_path / "data" / "nq_brain.db")
            )
            logger.info("تم ربط macro_bridge مع NQ_Brain بنجاح")
        else:
            logger.warning("مسار NQ_BRAIN_PATH غير موجود")
    else:
        logger.info("NQ_BRAIN_PATH غير مضبوط، سيتم تعطيل الربط الخارجي")
except Exception as e:
    logger.warning("فشل ربط macro_bridge مع NQ_Brain: %s", e)


class MacroBridge:
    def __init__(self) -> None:
        self.db_manager = db_manager

    def is_available(self) -> bool:
        return self.db_manager is not None

    def send_macro_data(
        self,
        indicator_name: str,
        current_time: str,
        actual_value: str,
        forecast_value: str,
        previous_value: str,
        impact_level: str,
    ) -> bool:
        if self.db_manager is None:
            logger.warning("MacroBridge غير متاح لأن db_manager غير مربوط")
            return False

        try:
            self.db_manager.save_macro_data(
                indicator=indicator_name,
                timestamp=current_time,
                actual=actual_value,
                forecast=forecast_value,
                previous=previous_value,
                impact=impact_level,
            )
            logger.info("تم إرسال بيانات الماكرو إلى NQ_Brain بنجاح")
            return True
        except Exception as e:
            logger.warning("فشل إرسال بيانات الماكرو إلى NQ_Brain: %s", e)
            return False
