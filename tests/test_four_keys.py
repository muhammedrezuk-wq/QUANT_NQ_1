# -*- coding: utf-8 -*-
"""المفاتيح الأربعة (ورقة المالك ٢٦-٠٨): السرعة · الأفق · الحدود + الرئيسي.

العقود: 50 = مثل اليوم حرفيًّا لكل مفتاح · الرئيسي إزاحة (القيمة−50) تضاف
للثلاثة معًا · الفردي (بما فيه النطاقي لكل أصل) يُضبط فوق الرئيسي ·
معامل الحدود L/50 محصور [×0.4، ×2.0] · القصّ إلى [1، 100].
العزل: بديل approved_value — لا قراءة ولا كتابة على السجل الحي.
"""
import pytest

from shared import analysis_speed as keys


@pytest.fixture()
def registry(monkeypatch):
    values: dict[tuple[str, str], float] = {}

    def fake(name, fallback, scope="global"):
        return values.get((name, scope), fallback)

    monkeypatch.setattr(keys, "approved_value", fake)
    return values


def test_match_point_all_keys_neutral(registry):
    """لا شيء معتمد ⇒ كل مفتاح 50 والمعامل ×1.0 — اليوم حرفيًّا."""
    assert keys.speed_value() == 50.0
    assert keys.horizon_value() == 50.0
    assert keys.limits_value() == 50.0
    assert keys.master_offset() == 0.0
    assert keys.limits_factor() == 1.0


def test_master_shifts_all_three_together(registry):
    """ورقة المالك §٤: رفع الرئيسي = أسرع·أضيق·أشدّ معًا وبنفس الدرجات."""
    registry[("MASTER_KEY", "global")] = 70.0
    assert keys.speed_value() == 70.0      # أسرع
    assert keys.horizon_value() == 70.0    # أضيق
    assert keys.limits_value() == 70.0     # أشدّ
    assert keys.limits_factor() == 1.4
    registry[("MASTER_KEY", "global")] = 30.0
    assert keys.speed_value() == 30.0      # أهدى · أوسع · أسهل
    assert keys.limits_factor() == 0.6


def test_individual_key_rides_above_master(registry):
    """«بتضل تقدر تدخل على أي مفتاح لحاله فوق ما عمله الرئيسي»."""
    registry[("MASTER_KEY", "global")] = 60.0        # إزاحة +10
    registry[("ANALYSIS_SPEED", "global")] = 75.0
    registry[("TRADING_HORIZON", "global")] = 20.0
    assert keys.speed_value() == 85.0                # 75 + 10
    assert keys.horizon_value() == 30.0              # 20 + 10
    assert keys.limits_value() == 60.0               # الحدود على الرئيسي وحده


def test_scoped_key_rides_above_global(registry):
    registry[("TRADING_HORIZON", "global")] = 40.0
    registry[("TRADING_HORIZON", "52992818\x1fXAUUSD")] = 90.0
    assert keys.horizon_value("52992818", "XAUUSD") == 90.0
    assert keys.horizon_value("52992818", "BTCUSD") == 40.0


def test_clamps_declared(registry):
    registry[("MASTER_KEY", "global")] = 100.0
    registry[("ANALYSIS_SPEED", "global")] = 90.0
    assert keys.speed_value() == 100.0               # سقف 100
    registry[("MASTER_KEY", "global")] = 1.0
    registry[("ANALYSIS_SPEED", "global")] = 10.0
    assert keys.speed_value() == 1.0                 # أرضية 1
    registry[("MASTER_KEY", "global")] = 50.0
    registry[("QUALITY_BAR", "global")] = 1.0
    assert keys.limits_factor() == 0.4               # أرضية المعامل المعلنة
    registry[("QUALITY_BAR", "global")] = 100.0
    assert keys.limits_factor() == 2.0               # سقفه المعلن


def test_limits_scale_thresholds_shape(registry):
    """شكل الأثر: عتبة مخزنة 35 مع حدود 70 ⇒ 35×1.4=49 (تشدّد) ومع 30 ⇒ 21."""
    registry[("QUALITY_BAR", "global")] = 70.0
    assert round(35.0 * keys.limits_factor(), 1) == 49.0
    registry[("QUALITY_BAR", "global")] = 30.0
    assert round(35.0 * keys.limits_factor(), 1) == 21.0
