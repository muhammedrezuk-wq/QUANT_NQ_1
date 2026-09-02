"""ورقة الحلول §٣ — مصدر الحقيقة الوحيد لهوية الدورة.

لماذا ملفّ مستقلّ ولماذا **ممنوع** بانٍ محلّي في أيّ ذرّة:
    كانت ٦٧ ذرّة منتِجة تبني المعرِّف بنفسها بالصيغة
    `"%s|%s|%s" % (symbol, timeframe, period_start)`.
    وهذا عطلان:
    ١) **هوية ناقصة** — حسابان على وسيطين مختلفين، نفس الرمز والفريم
       وnفس `period_start`، يتقاسمان معرِّفًا واحدًا. ومَن يفهرس دوراته
       بالمعرِّف يخلط الحسابين في دورة واحدة.
    ٢) **لا صاحب للتعريف** — ٦٧ نسخة من الصيغة نفسها. تعديل واحدة
       دون البقيّة يقطع الربط بين المنتِج والمستهلك بصمت، ولا يسقط
       حارس لأنّ كلّ ذرّة «صحيحة» وحدها.

    ⇒ التعريف هنا وحده. أيّ ذرّة تبني المعرِّف تستدعي `cycle_key` أو
      `cycle_key_of`. وبقاء بانٍ محلّي في ذرّة يُعدّ مخالفة للعقد.

الصيغة:
    account_id | broker | symbol | timeframe | period_start

    الفاصل `|`، وأيّ `|` داخل مكوِّن يُرمَّز `%7C` كي يبقى التفكيك
    عكسيًّا ولا يُنتج مكوّنان مختلفان معرِّفًا واحدًا.

⛔ هذا الملفّ لا يقرّر جاهزيةً ولا يحسب رقمًا ماليًّا. يبني معرِّفًا فقط.
"""
from __future__ import annotations

from typing import Any, Mapping

FIELD_SEPARATOR = "|"
_ESCAPED_SEPARATOR = "%7C"

#: ترتيب الحقول في المعرِّف — معلَن كي يُقرأ المعرِّف بلا تخمين.
CYCLE_FIELDS = ("account_id", "broker", "symbol", "timeframe", "period_start")


def _part(value: Any) -> str:
    """مكوِّن واحد نصًّا، بفاصل مُرمَّز كي لا يتسرّب إلى بنية المعرِّف."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.strip().replace(FIELD_SEPARATOR, _ESCAPED_SEPARATOR)


def cycle_key(*, account_id: Any, broker: Any, symbol: Any,
              timeframe: Any, period_start: Any) -> str:
    """معرِّف الدورة الكامل — §٣.

    حسابان مختلفان، أو وسيطان مختلفان، على نفس الرمز والفريم و
    `period_start` **لا يمكن** أن يتقاسما المعرِّف نفسه.
    """
    return FIELD_SEPARATOR.join((
        _part(account_id), _part(broker), _part(symbol),
        _part(timeframe), _part(period_start)))


def cycle_key_of(payload: Mapping[str, Any] | Any, *, symbol: Any = None,
                 timeframe: Any = None, period_start: Any = None) -> str:
    """يبني المعرِّف من حمولة الحدث، مع تجاوز صريح للحقول عند الحاجة.

    الهوية (`account_id` · `broker`) تُقرأ من الحمولة ولا تُخمَّن ولا
    تُستعاض عنها بقيمة افتراضية (§٢). حمولة بلا هوية تُنتج معرِّفًا
    بمكوّنَين فارغين — يُكشف ولا يُستَر.
    """
    if not isinstance(payload, Mapping):
        payload = {}
    if symbol is None:
        symbol = payload.get("symbol") or payload.get("asset")
    if timeframe is None:
        timeframe = payload.get("timeframe", "")
    if period_start is None:
        period_start = payload.get("period_start", payload.get("timestamp", ""))
    return cycle_key(
        account_id=payload.get("account_id"), broker=payload.get("broker"),
        symbol=symbol, timeframe=timeframe, period_start=period_start)


def split_cycle_key(cycle_id: Any) -> dict[str, str]:
    """يفكّ المعرِّف إلى حقوله — للتشخيص والحرّاس، لا لمنطق القرار.

    معرِّف بعدد حقول مختلف يُعاد كما هو تحت `raw` بدل تخمين ترتيبه.
    """
    text = "" if cycle_id is None else str(cycle_id)
    parts = text.split(FIELD_SEPARATOR)
    if len(parts) != len(CYCLE_FIELDS):
        return {"raw": text}
    return {name: part.replace(_ESCAPED_SEPARATOR, FIELD_SEPARATOR)
            for name, part in zip(CYCLE_FIELDS, parts)}
