"""محرك شخصية الأفق — الظل (عقد «ورقة المالك — المؤشر الموحد v1.0»، ٢٦-٠٨).

ينفّذ معادلات المالك الحرفية (§9–§41) التي تولّد الشخصية الكاملة من رقم واحد.
**المصدر الحي هو مفتاح الأفق** `TRADING_HORIZON` (ورقة المفاتيح الأربعة ٢٦-٠٨:
الأفق مستقل عن السرعة — «بتصطاد غزال بإيد سريعة أو هادية»؛ أعلى = أضيق/سكالب)،
والتحويل المعلَن إلى مؤشر الورقة الموحدة (أدنى = أقصر):

    I = 101 − المفتاح        ⇒        x = (I − 1)/99 = (100 − المفتاح)/99

**هذا ظلٌّ بمراحل هجرة المالك نفسها (§61):** يولّد ويُنشر ويُقارن —
**ولا يطبّق شيئًا على أي ذرّة** حتى أمر تفعيل صريح منه (Phase 4).
طبقة المخاطرة خارج هذا المحرك كليًّا: عيار المخاطرة يعمل بعقد المحورين
v1.1 §3 المختوم (تصحيح المالك: فصل الزيادة عن الخفض — لا صيغة E×D القديمة).
"""
from __future__ import annotations

import os
import time
from typing import Any

ENGINE_VERSION = "1.1.0"
FORMULA_VERSION = "HORIZON_PROFILE_V1"

#: مفتاح التفعيل (أمر المالك «فعل» ٢٦-٠٨): 0 = ظل، 1 = الشخصية المولَّدة سارية.
#: القيمة السارية بالكود 0 (ظل) — والتفعيل الحي صفُّ اعتمادٍ باسم كلمة المالك،
#: فيبقى الأسطول على الظل (سجل الفحوص معزول) ورجوعه كبسة واحدة من اللوحة.
ACTIVE_NAME = "HORIZON_PROFILE_ACTIVE"

#: خريطة الشخصية ← عيارات القرار الحية (طبقة القرار و166 فقط):
#: النوافذ ملك مفتاح السرعة وعتبات المحللين ملك مفتاح الحدود (ورقة المفاتيح
#: الأربعة الأحدث تعلو)، و413 خارج سجل العيارات و523 يقوده مفتاح الأفق أصلًا
#: ومنحنى E/H عقد مختوم — كلها مستثناة معلَنة.
#: كل قيمة: (قسم البروفايل، حقله).
PROFILE_DIAL_MAP: dict[str, tuple[str, str]] = {
    "ANALYSIS_FAST_WEIGHT": ("166", "fast_weight"),
    "ANALYSIS_SLOW_WEIGHT": ("166", "slow_weight"),
    "ANALYSIS_FAST_REQUIRED_DEPTH": ("166", "fast_required_depth"),
    "ANALYSIS_SLOW_REQUIRED_DEPTH": ("166", "slow_required_depth"),
    "DECISION_LIVE_STALE_AFTER_S": ("166", "live_stale_after_s"),
    "DECISION_NEUTRAL_BAND": ("458", "neutral_band"),
    "DECISION_CONFLICT_RATIO": ("458", "conflict_ratio"),
    "DECISION_MIN_PARTICIPATION": ("453", "min_participation"),
    "DECISION_CONTEXT_WEIGHT": ("453", "context_weight"),
    "DECISION_MIN_CONFIDENCE": ("453", "min_confidence"),
    "DECISION_LOW_QUALITY_FACTOR": ("452", "low_quality_factor"),
    "DECISION_BUY_MIN_DIRECTION": ("455_456", "min_direction"),
    "DECISION_SELL_MIN_DIRECTION": ("455_456", "min_direction"),
    "DECISION_ELIGIBILITY_MIN_CONFIDENCE": ("455_456", "min_confidence"),
    "DECISION_MIN_STRENGTH": ("455_456", "min_strength"),
    "DECISION_MIN_CURRENT_DEPTH": ("455_456", "min_current_depth"),
}

_OVR_RECHECK_S = 2.0
_ovr_cache: dict[str, Any] = {"at": -1.0, "mtime": -1.0, "map": None,
                              "enter_exit": None, "path": None}

#: §4 — حدود الأفق الزمني: 0.25 ثانية ← 180 يومًا (15,552,000 ث).
H_MIN_S = 0.25
H_MAX_S = 15_552_000.0

#: §42 — منحنى E/H الست نقاط: ثابت في الإصدار الأول، لا يولّده المؤشر.
EXPOSURE_CURVE = (
    (0.00, 0.00, 1.00), (0.20, 0.08, 0.75), (0.40, 0.18, 0.55),
    (0.60, 0.30, 0.35), (0.80, 0.42, 0.25), (1.00, 0.50, 0.20),
)


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _r2(value: float) -> float:
    return round(value, 2)


def _r4(value: float) -> float:
    return round(value, 4)


def normalized_x(speed: float) -> float:
    """§3 مع تحويل السلّم: x=0 عند أقصى سرعة (السرعة 100) و1 عند أبطئها (1)."""
    try:
        s = float(speed)
    except (TypeError, ValueError):
        raise ValueError("INVALID_PROFILE_INDEX") from None
    if s != s or s < 1.0 or s > 100.0:
        # §53 — خارج المجال: لا يُولَّد شيء ولا يُعدَّل عيار.
        raise ValueError("INVALID_PROFILE_INDEX")
    return _clamp((100.0 - _r2(s)) / 99.0, 0.0, 1.0)


def horizon_seconds(x: float) -> float:
    """§4/§37 — الزمن يتوسع أسّيًّا: H(x) = H_min × (H_max/H_min)^x."""
    return H_MIN_S * (H_MAX_S / H_MIN_S) ** x


def _fast_path_weights(x: float) -> dict[str, float]:
    """§20/§21 — التوزيع المستمر ثم إعادة التطبيع إلى 100 (والحجم صفر دائمًا)."""
    raw = {
        "velocity": 25.0 + 2.0 * x,
        "momentum": 20.0 + 2.0 * x,
        "acceleration": 20.0 + 3.0 * x,
        "spread": 12.0 - 1.0 * x,
        "volatility": 8.0,
        "noise": 10.0 - 4.0 * x,
        "volume_quality": 5.0 - 1.0 * x,
    }
    total = sum(raw.values())
    weights = {name: _r4(value * 100.0 / total) for name, value in raw.items()}
    weights["volume"] = 0.0
    return weights


def _registry_rows() -> tuple[float, dict[str, dict[str, Any]]]:
    from shared.parameter_registry import ParameterRegistry
    registry = ParameterRegistry()
    try:
        mtime = os.path.getmtime(registry.path)
    except OSError:
        mtime = -1.0
    rows = {str(row["name"]): row for row in registry.all()
            if str(row.get("scope")) == "global"}
    return mtime, rows


def _rebuild_overrides() -> None:
    """يبنى مرة كل تغيّر قاعدة (بصمة ملف + خانق ثانيتين) — لا على المسار الساخن."""
    from shared.analysis_speed import horizon_value
    now = time.monotonic()
    if _ovr_cache["map"] is not None and now - _ovr_cache["at"] < _OVR_RECHECK_S:
        return
    _ovr_cache["at"] = now
    try:
        mtime, rows = _registry_rows()
    except Exception:  # noqa: BLE001 — قاعدة مقفولة لحظيًّا: أبقِ آخر حالة
        return
    horizon = horizon_value()
    if (_ovr_cache["map"] is not None and mtime == _ovr_cache["mtime"]
            and _ovr_cache.get("horizon") == horizon):
        return
    _ovr_cache["mtime"] = mtime
    _ovr_cache["horizon"] = horizon
    active_row = rows.get(ACTIVE_NAME)
    active = (active_row is not None
              and str(active_row.get("status")) == "APPROVED"
              and float(active_row.get("value") or 0.0) >= 0.5)
    if not active:
        _ovr_cache["map"] = {}
        _ovr_cache["enter_exit"] = None
        return
    activated_at = float(active_row.get("approved_at") or 0.0)
    try:
        profile = generate(horizon)
    except ValueError:
        _ovr_cache["map"] = {}
        _ovr_cache["enter_exit"] = None
        return
    overrides: dict[str, float] = {}
    for name, (section, field) in PROFILE_DIAL_MAP.items():
        row = rows.get(name)
        # يد المالك بعد التفعيل تعلو المولَّد (قاعدة «الفردي فوق»):
        # صف اعتمده بعد لحظة التفعيل يمسك، وما قبلها يحلّ محله المولَّد.
        if (row is not None and str(row.get("status")) == "APPROVED"
                and float(row.get("approved_at") or 0.0) > activated_at):
            continue
        value = profile.get(section, {}).get(field)
        if value is not None:
            overrides[name] = float(value)
    _ovr_cache["map"] = overrides
    _ovr_cache["enter_exit"] = (float(profile["581"]["s_enter"]),
                                float(profile["581"]["s_exit"]))


def profile_active() -> bool:
    _rebuild_overrides()
    return bool(_ovr_cache["map"])


def dial_override(name: str) -> float | None:
    """قيمة العيار المولَّدة إن كانت الشخصية سارية والاسم ضمن الخريطة —
    وإلا None فيبقى الساري هو المعتمد/المانيفست كما كان."""
    _rebuild_overrides()
    overrides = _ovr_cache["map"] or {}
    return overrides.get(name)


def hysteresis_override(default_enter: float,
                        default_exit: float) -> tuple[float, float]:
    """هستيريسيس 581 المولَّد عند التفعيل (§41) — وإلا قيم المانيفست المختومة."""
    _rebuild_overrides()
    pair = _ovr_cache["enter_exit"]
    return pair if pair is not None else (default_enter, default_exit)


def generate(speed: float) -> dict[str, Any]:
    """الشخصية الكاملة من سرعة واحدة — كل معادلة بنصّ ورقة المالك وفقرتها."""
    x = normalized_x(speed)
    ema_fast = round(5 + 19 * x)
    ema_slow = round(12 + 43 * x)
    if ema_slow <= ema_fast + 2:            # §16 — يُرفع البطيء آليًّا.
        ema_slow = ema_fast + 3
    fast_weight = _r4(90.0 - 25.0 * x)      # §9
    return {
        # §55 — لا رقم بلا مصدر.
        "profile_index": _r2(101.0 - _r2(float(speed))),
        "source_key": "TRADING_HORIZON",
        "key_value": _r2(float(speed)),
        "x": round(x, 6),
        "formula_version": FORMULA_VERSION,
        "engine_version": ENGINE_VERSION,
        "horizon_seconds": round(horizon_seconds(x), 3),
        "166": {
            "fast_weight": fast_weight,
            "slow_weight": _r4(100.0 - fast_weight),
            "live_stale_after_s": _r4(1.5 + 4.5 * x),      # §10 (الصيغة المعتمدة)
            "fast_required_depth": _r4(42.0 + 28.0 * x),   # §11
            "slow_required_depth": _r4(30.0 + 35.0 * x),
        },
        "fast_path_weights": _fast_path_weights(x),
        "151": {"ema_fast": ema_fast, "ema_slow": ema_slow,
                "slope_lookback": round(2 + 3 * x)},        # §16/§17
        "152": {"roc_period": round(3 + 9 * x),             # §15
                "impulse_window": round(5 + 11 * x),
                "persistence_window": round(8 + 8 * x),
                "persistence_min": _r4(0.52 + 0.08 * x)},
        "153": {"atr_window": round(14 + 16 * x),           # §18
                "baseline_window": round(32 + 28 * x),
                "stddev_window": round(16 + 16 * x)},
        "155": {"baseline_window": round(24 + 36 * x),      # §19
                "exp_short": round(5 + 7 * x),
                "exp_long": round(20 + 20 * x)},
        "162": {"baseline_window": round(16 + 28 * x ** 1.20)},   # §12
        "163": {"baseline_window": round(16 + 28 * x ** 1.20),    # §13
                "accel_ratio": _r4(0.65 - 0.15 * x)},
        "165": {"window": round(12 + 18 * x ** 1.25),       # §14
                "noisy_max": _r4(0.30 + 0.10 * x),
                "efficient_min": _r4(0.60 - 0.10 * x)},
        "453": {"min_participation": _r4(0.30 + 0.15 * x ** 0.80),  # §22
                "min_confidence": _r4(0.60 - 0.15 * x),     # §23
                "context_weight": _r4(0.06 - 0.035 * x)},   # §24
        "452": {"low_quality_factor": _r4(0.50 - 0.18 * x)},        # §25
        "458": {"neutral_band": _r4(0.05 + 0.05 * x),       # §26
                "conflict_ratio": _r4(0.50 + 0.10 * x)},    # §27
        "413": {"min_active_weight": _r4(40.0 - 13.0 * x),  # §28
                "confidence_threshold": _r4(60.0 - 10.0 * x),
                "required_depth": _r4(60.0 - 15.0 * x)},
        "455_456": {"min_direction": _r4(55.0 + 3.0 * x),   # §31–§34
                    "min_strength": _r4(50.0 + 3.0 * x),
                    "min_confidence": _r4(60.0 + 2.0 * x),
                    "min_current_depth": 50.0},
        "457": {"min_strength": 45.0, "min_confidence": 55.0,
                "min_current_depth": 45.0},                 # §35 — لا يتدرج
        "523": {"filter_strength": _r4(0.30 + 0.50 * x),    # §38
                "mgmt_cadence_s": _r4(2.0 + 13.0 * x),      # §39
                "stop_min_frac": round(0.0010 + 0.0040 * x, 6),   # §40
                "stop_max_frac": round(0.0030 + 0.0070 * x, 6)},
        "581": {"s_enter": _r4(0.35 + 0.03 * x),            # §41
                "s_exit": _r4(0.20 + 0.05 * x)},
        "exposure_curve": [list(point) for point in EXPOSURE_CURVE],  # §42 ثابت
    }
