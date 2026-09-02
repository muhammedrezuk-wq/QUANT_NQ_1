"""كتابة JSON ذرّية — إمّا الملفّ القديم كاملًا أو الجديد كاملًا، لا شيء بينهما.

السبب المقيس: كل ملفّات الحالة (`users.json` · `system_state.json` ·
`translations_ar.json` · `calendar_sent.json`) كانت تُكتب فوق نفسها مباشرة.
انقطاع كهرباء أو قتل العملية وسط الكتابة يترك ملفًّا مبتورًا — و`users.json`
المبتور يعني بوتًا بلا مالك ولا قائمة مستخدمين، ولا رجعة.

الطريقة: يُكتب ملفّ مؤقّت بجانب الهدف، يُدفَع إلى القرص فعليًّا
(`flush` ثمّ `fsync`)، ثمّ `os.replace` — وهي عملية ذرّية على ويندوز
ولينكس كليهما. فإن سقط النظام قبل السطر الأخير بقي الملفّ القديم سليمًا.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def write_json_atomic(path: str, data: Any, indent: int | None = None) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=directory,
        prefix=".tmp-",
        suffix=".json",
        delete=False,
    )
    try:
        with handle:
            json.dump(data, handle, ensure_ascii=False, indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
