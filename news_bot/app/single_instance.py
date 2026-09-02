"""حارس النسخة الواحدة — ختم المالك ٢٠٢٦-٠٨-٢٠.

سببه المقيس: كبستان بالغلط على المشغّل ⇒ نسختان من البوت بنفس التوكن ⇒
تلغرام يرفض بـ`409 Conflict: terminated by other getUpdates request` بلا توقّف،
وكل نسخة تقطع الثانية فلا يعمل البوت أصلًا.

الطريقة: **قفل ملفّ على مستوى نظام التشغيل** (`msvcrt.locking` على ويندوز).
لماذا لا ملفّ PID: ملفّ الـPID يبقى بعد انهيار البوت فيمنع التشغيل ظلمًا
(«قفل يتيم»). قفل النظام **يُحرَّر تلقائيًّا عند موت العملية** مهما كانت
طريقة موتها — فلا يحتاج تنظيفًا يدويًّا أبدًا.

الاستعمال:
    lock = acquire("data/bot.lock")
    if lock is None:   # نسخة أخرى شغّالة
        ...
"""

from __future__ import annotations

import os
from typing import Optional, TextIO

try:
    import msvcrt  # ويندوز
except ImportError:  # pragma: no cover - المشروع ويندوز
    msvcrt = None  # type: ignore

try:
    import fcntl  # لينكس/ماك (وحدة النشر deploy/*.service)
except ImportError:
    fcntl = None  # type: ignore


def acquire(lock_path: str) -> Optional[TextIO]:
    """يحجز القفل ويعيد المقبض، أو None إذا كانت نسخة أخرى ماسكة له.

    المقبض يجب أن يبقى مفتوحًا طوال حياة العملية — إغلاقه يحرّر القفل.
    """
    directory = os.path.dirname(lock_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    try:
        handle = open(lock_path, "a+")
    except OSError:
        return None

    try:
        handle.seek(0)
        if msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            # بلا آلية قفل معروفة: لا ندّعي حماية غير موجودة
            return handle
    except OSError:
        handle.close()
        return None

    try:
        handle.truncate(0)
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass
    return handle


def release(handle: Optional[TextIO]) -> None:
    if handle is None:
        return
    try:
        handle.seek(0)
        if msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass
