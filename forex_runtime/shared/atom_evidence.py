"""البند ٤ — تعريف واحد لعمق الذرّة الداخلية.

المشكلة التي يحلّه:
    ذرّات الأقسام الداخلية لا ترسل `current_depth`، فالعقد يُعلنه
    `UNKNOWN` — وهذا صادق لكنّه فارغ. والذرّة التي **تملك** نافذة أدلّة
    خاصّة بها تعرف عمقها فعلًا: كم امتلأت نافذتها من المطلوب.

⛔ ما لا يفعله هذا الملفّ — وهو الأهمّ:
    ١) **لا يخترع `strength`.** لا دالّة هنا تُنتج قوّة. الذرّة بلا تعريف
       قوّة معتمد تبقى `strength = UNKNOWN`، ولا تُملأ صفرًا لتَعبُر.
    ٢) **لا يفترض `directional`.** الاتجاهية جزء من تعريف الذرّة، لا
       صفةٌ تُلصق بها من الخارج.
    ٣) **لا يعطي عمقًا لذرّة بلا نافذة.** القياس أثبت أنّ ٢٤ ذرّة من ٤٢
       تستهلك مخرَج غيرها ولا تملك نافذة أدلّة خاصّة. عمق هذه **مجهول**
       حتّى يُعرَّف كيف يُورَث من مدخلاتها — وفرضُ رقم عليها اختراع.
    ٤) **لا يقرّر جاهزية.** البوّابة في `section_contract` وحدها، وتحت
       حاجز `UNAPPROVED_PARAMETER`.

العمق هنا حقيقة واحدة لا غير: **نسبة امتلاء نافذة الذرّة نفسها.**
وهي ليست زمنًا ولا `sleep` ولا عدد ثوانٍ (§٦).
"""
from __future__ import annotations

from typing import Any

#: العمق المطلوب الافتراضيّ لذرّة داخلية: نافذتها كاملة.
#: ⛔ ليس رقمًا مُعايَرًا — هو تعريف «الاكتمال» لا عتبة قرار. عتبة القرار
#:   تأتي من `required_depth` المعايَر في `section_live`/`live_analysis`.
FULL_WINDOW = 100.0


def window_depth(have: Any, need: Any) -> float | None:
    """عمق الذرّة = نسبة ما جمعته إلى ما تحتاجه. `None` إن كان مجهولًا.

    `None` — لا `0.0` — حين لا تُعرف النافذة: الصفر يُقرأ قياسًا،
    والمجهول ليس قياسًا (§١٠).
    """
    try:
        collected = int(have)
        required = int(need)
    except (TypeError, ValueError):
        return None
    if required <= 0:
        return None
    return max(0.0, min(100.0, collected / required * 100.0))


def window_evidence(*, have: Any, need: Any) -> dict[str, float]:
    """حقلا العمق جاهزين للدمج في حمولة تُنشَر مباشرةً.

    نافذة مجهولة ⇒ قاموس فارغ ⇒ العقد يُعلن `current_depth` مجهولًا،
    ولا يُدسّ صفرٌ يُقرأ قياسًا.

    `data_completeness` (§١١) هو نفس نسبة امتلاء النافذة — تعريف اكتمال
    البيانات عند هذه الطبقة، منفصل اسميًّا عن `current_depth` حتى تستطيع
    ذرّة تحسب نضج تحليل حقيقي (مثل ٣٠١) أن تستبدل `current_depth` بقياسها
    الخاص بلا أن تفقد رقم الاكتمال الخام.
    """
    depth = window_depth(have, need)
    if depth is None:
        return {}
    return {
        "current_depth": round(depth, 4),
        "required_depth": FULL_WINDOW,
        "data_completeness": round(depth, 4),
    }


def inherit_evidence(source: Any) -> dict[str, Any]:
    """عمق وزمن **موروثان** من الحدث المسبِّب — للذرّة التي لا نافذة لها.

    الاستراتيجية لا تجمع أدلّة بنفسها: تقرأ مخرَج محلّل. فعمقها هو عمق
    مدخلها بالضرورة السببيّة، لا رقم يُخترع لها.

    ⛔ ما لا يُورَّث: `strength` — القوّة تعريفُ مجالٍ لا قيمةٌ تُمرَّر.
       ولا `direction` ولا `state`: لكلّ ذرّة رأيها وحالتها.
    ⛔ ومدخلٌ بلا عمق ⇒ قاموس فارغ ⇒ يبقى `UNKNOWN`، لا صفرًا.
    """
    if not isinstance(source, dict):
        return {}
    out: dict[str, Any] = {}
    for field in ("current_depth", "required_depth", "source_timestamp"):
        value = source.get(field)
        if value is not None:
            out[field] = value
    unified = source.get("unified")
    if isinstance(unified, dict) and "current_depth" not in out:
        for field in ("current_depth", "required_depth"):
            value = unified.get(field)
            if value is not None and field not in unified.get("unknown_fields", []):
                out[field] = value
    return out


def stamp_window(payload: dict[str, Any], *, have: Any, need: Any,
                 source_timestamp: Any = None) -> dict[str, Any]:
    """يضيف عمق النافذة والزمن إلى حمولة ذرّة — ولا يمسّ رأيها.

    ⛔ لا يكتب `strength` ولا `direction` ولا `state`.
    """
    if not isinstance(payload, dict):
        return payload
    depth = window_depth(have, need)
    if depth is not None:
        payload["current_depth"] = round(depth, 4)
        payload["required_depth"] = FULL_WINDOW
    if source_timestamp is not None and "source_timestamp" not in payload:
        payload["source_timestamp"] = source_timestamp
    return payload
