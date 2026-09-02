from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler


# ختم المالك ٢٠٢٦-٠٨-٢٠: التوكن ظهر داخل السجلّ.
# المصدر المقيس: مكتبة httpx تسجّل الرابط كاملًا عند كل طلب —
#   "HTTP Request: POST https://api.telegram.org/bot<التوكن>/getUpdates"
# فيتسرّب المفتاح إلى ملفّ السجلّ وإلى الشاشة ومن ثمّ إلى أي لقطة تُرسَل.
# الحلّ طبقتان: (١) مرشّح يخفي النمط من كل سطر مهما كان مصدره،
#               (٢) إسكات httpx عند INFO فلا يطبع الروابط أصلًا.
_TOKEN_RE = re.compile(r"bot\d{6,}:[A-Za-z0-9_\-]{20,}")
_MASK = "bot***:***"


def mask_secrets(text: str) -> str:
    return _TOKEN_RE.sub(_MASK, text)


class _RedactFilter(logging.Filter):
    """يخفي التوكن من الرسالة ومن وسائطها قبل أن تُكتب."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str) and "bot" in record.msg:
                record.msg = mask_secrets(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: (mask_secrets(v) if isinstance(v, str) else v)
                                   for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(mask_secrets(a) if isinstance(a, str) else a
                                        for a in record.args)
        except Exception:
            # المرشّح لا يُسقط سطر سجلّ أبدًا — الأسوأ أن نفقد الخبر كلّه
            pass
        return True


def setup_logger(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "bot.log")

    # تدوير السجلّ: كان `FileHandler` عاديًّا بلا حدّ، فبلغ الملفّ ٣ ميغا
    # و١٩٣٨٠ سطرًا — أكثره سطران في الدقيقة من `apscheduler` عن نبضة الأجندة.
    # خمسة ملفّات × ٢ ميغا سقف معلوم، والأقدم يسقط تلقائيًّا.
    file_handler = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    stream_handler = logging.StreamHandler()
    redact = _RedactFilter()
    file_handler.addFilter(redact)
    stream_handler.addFilter(redact)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[file_handler, stream_handler],
    )

    # httpx يطبع رابط كل طلب عند INFO — وفيه التوكن. لا نحتاجه، ونكتفي بالأخطاء.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # نبضة الأجندة تعمل كل دقيقة، وسطراها «بدأت/نجحت» يغرقان السجلّ بلا خبر.
    # فشل المهمّة يبقى ظاهرًا: مستواه أعلى من WARNING.
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)

    return logging.getLogger("QUANT_NQ_NEWS")
