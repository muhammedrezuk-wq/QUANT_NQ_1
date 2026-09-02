"""مفتاح سرعة التحليل — عقد «ورقة معايير سرعة التحليل v1.0» + ملحقها الشفهي (٢٦-٠٨).

المفتاح الواحد الذي أمر به المالك: «مفتاح واحد يعاير كل أقسام يسرعهم أو يبطئهم».

العقد المعلَن (لا قفزات مخفية — بند الإغلاق 9):
    ANALYSIS_SPEED ∈ [1.00, 100.00]
    نقطة التطابق 50.00 = سلوك اليوم حرفيًّا (كل نافذة تساوي قيمتها الحالية).
    factor(speed) = clamp(50 / speed, 0.2, 5.0)
        50 → ×1.0 (اليوم) · 100 → ×0.5 (أسرع: ذاكرة أقصر ودليل يمتلئ أسرع)
        25 → ×2.0 (أبطأ) · ≤10 → ×5.0 (سقف البطء المعلَن)
    النافذة المشتقة = clamp(round(الأساس × factor), الأرضية الإحصائية, 63)
        الأرضيات معلَنة عند كل استعمال (§11: لا نافذة تصير صفرًا)،
        والسقف 63 هو طول ذاكرة المراكم نفسها (deque maxlen).

النطاق (ملحق المالك: سرعة لكل حساب ولكل أصل):
    قيمة النطاق (حساب␟رمز) المعتمدة تعلو، وإلا العامة المعتمدة، وإلا 50.00.
    جاهز من اليوم — القيم النطاقية تُعتمد حين تتعدد الحسابات/الأصول.

الفصل الصارم (§6/§33): هذا الملف لا يقرؤه أي ملف مخاطر —
    لا 581 ولا 508 ولا 516 ولا 518 ولا 584 ولا 552. السرعة تغيّر التحليل فقط.
"""
from __future__ import annotations

from shared.parameter_registry import approved_value

SPEED_NAME = "ANALYSIS_SPEED"
#: ورقة المفاتيح الأربعة (٢٦-٠٨ بصوت المالك): السرعة · الأفق · الحدود + الرئيسي.
HORIZON_NAME = "TRADING_HORIZON"
LIMITS_NAME = "QUALITY_BAR"
MASTER_NAME = "MASTER_KEY"
MATCH_POINT = 50.0
_KEY_MID = 50.0
_SEP = "\x1f"
_FACTOR_MIN = 0.2
_FACTOR_MAX = 5.0
#: معامل الحدود: L/50 محصورًا — عند 50 = ×1.0 (اليوم حرفيًّا).
_LIMITS_MIN = 0.4
_LIMITS_MAX = 2.0
#: سقف كل نافذة مشتقة = طول ذاكرة العوائد في المراكم (deque maxlen=63).
_DEQUE_CAP = 63


def _scoped(name: str, account_id: str, symbol: str, fallback: float) -> float:
    """قيمة مفتاح لنطاقه: (حساب␟رمز) المعتمدة ← العامة المعتمدة ← الافتراض."""
    base = approved_value(name, fallback)
    if account_id and symbol:
        return approved_value(name, base,
                              scope=str(account_id) + _SEP + str(symbol))
    return base


def master_offset(account_id: str = "", symbol: str = "") -> float:
    """المفتاح الرئيسي (ورقة المالك §٤): إزاحة (القيمة − 50) تُضاف للمفاتيح
    الثلاثة معًا — رفعه = أسرع·أضيق·أشدّ، وخفضه = أهدى·أوسع·أسهل — والفردي
    يبقى قابلًا للضبط فوقه. عند 50 = محايد (اليوم حرفيًّا)."""
    return _scoped(MASTER_NAME, account_id, symbol, _KEY_MID) - _KEY_MID


def _with_master(base: float, account_id: str, symbol: str) -> float:
    value = base + master_offset(account_id, symbol)
    return 1.0 if value < 1.0 else 100.0 if value > 100.0 else value


def speed_value(account_id: str = "", symbol: str = "") -> float:
    """مفتاح السرعة الساري (بعد إزاحة الرئيسي): أعلى = نوافذ أقصر وتفاعل أسرع."""
    return _with_master(_scoped(SPEED_NAME, account_id, symbol, MATCH_POINT),
                        account_id, symbol)


def horizon_value(account_id: str = "", symbol: str = "") -> float:
    """مفتاح الأفق الساري (بعد إزاحة الرئيسي): أعلى = أضيق/سكالب، أدنى = سوينغ."""
    return _with_master(_scoped(HORIZON_NAME, account_id, symbol, _KEY_MID),
                        account_id, symbol)


def limits_value(account_id: str = "", symbol: str = "") -> float:
    """مفتاح الحدود الساري (بعد إزاحة الرئيسي): أعلى = عتبات أشدّ (زبالة أقل)."""
    return _with_master(_scoped(LIMITS_NAME, account_id, symbol, _KEY_MID),
                        account_id, symbol)


def limits_factor(account_id: str = "", symbol: str = "") -> float:
    """معامل شدّة القبول: L/50 محصورًا [×0.4، ×2.0] — يضرب عتبات المحللين
    (العمق المطلوب/الثقة/القوة). عند 50 = ×1.0 فلا يتغير شيء عن اليوم."""
    factor = limits_value(account_id, symbol) / _KEY_MID
    return _LIMITS_MIN if factor < _LIMITS_MIN else \
        _LIMITS_MAX if factor > _LIMITS_MAX else factor


def speed_factor(speed: float) -> float:
    """معامل تحجيم النوافذ — معلَن: 50/speed محصورًا [0.2, 5.0]."""
    try:
        value = float(speed)
    except (TypeError, ValueError):
        value = MATCH_POINT
    if value != value or value <= 0.0:
        value = MATCH_POINT
    factor = MATCH_POINT / value
    if factor < _FACTOR_MIN:
        return _FACTOR_MIN
    if factor > _FACTOR_MAX:
        return _FACTOR_MAX
    return factor


def window(base: int, factor: float, floor: int) -> int:
    """نافذة مشتقة: الأساس×المعامل، بأرضية إحصائية معلنة وسقف ذاكرة المراكم."""
    derived = int(round(base * factor))
    if derived < floor:
        return floor
    if derived > _DEQUE_CAP:
        return _DEQUE_CAP
    return derived
