# -*- coding: utf-8 -*-
"""تفعيل شخصية الأفق (أمر المالك «فعل» ٢٦-٠٨) — العقد:

0 (الافتراض بالكود) = ظل: لا تغيير على أي عيار — الأسطول كله يعمل هكذا.
صف اعتماد ACTIVE=1 = المولَّدة تسري محل عيارات القرار/166/هستيريسيس 581.
يد المالك بعد لحظة التفعيل تعلو المولَّد (صف أحدث اعتمادًا يمسك).
ACTIVE=0 لاحقًا = إطفاء كامل بكبسة.
السجل هنا هو السجل المؤقت المعزول (conftest) — لا لمس للحي.
"""
from shared import horizon_profile as hp
from shared.analysis_speed import horizon_value
from shared.decision_dials import effective_value
from shared.parameter_registry import ParameterRegistry, refresh_gate


def _reset_cache():
    hp._ovr_cache.update({"at": -1.0, "mtime": -1.0, "map": None,
                          "enter_exit": None, "horizon": None})
    refresh_gate()


def _approve(name, value, at):
    from shared.decision_dials import declare
    registry = ParameterRegistry()
    declare(registry)
    registry.approve(name, value=value, source="OWNER",
                     approved_by="nq-test", command_id="act-%s-%s" % (name, at),
                     approved_at=at)
    _reset_cache()


def test_default_is_shadow_identity():
    _reset_cache()
    assert hp.profile_active() is False
    assert hp.dial_override("ANALYSIS_FAST_WEIGHT") is None
    assert hp.hysteresis_override(0.20, 0.15) == (0.20, 0.15)
    assert effective_value("ANALYSIS_FAST_WEIGHT", 55.0) == 55.0


def test_activation_applies_generated_and_hand_after_wins():
    # عيار اعتُمد قبل التفعيل (كالعتبات المختومة) — المولَّد يحل محله.
    _approve("DECISION_NEUTRAL_BAND", 0.05, at=10.0)
    # التفعيل بكلمته.
    _approve(hp.ACTIVE_NAME, 1.0, at=100.0)
    assert hp.profile_active() is True
    generated = hp.generate(horizon_value())
    assert hp.dial_override("ANALYSIS_FAST_WEIGHT") == generated["166"]["fast_weight"]
    assert effective_value("ANALYSIS_FAST_WEIGHT", 55.0) == generated["166"]["fast_weight"]
    # المعتمد قبل التفعيل لا يعلو: الحياد المولَّد يسري.
    assert effective_value("DECISION_NEUTRAL_BAND", 0.05) == generated["458"]["neutral_band"]
    # هستيريسيس 581 المولَّد يسري.
    assert hp.hysteresis_override(0.20, 0.15) == (generated["581"]["s_enter"],
                                                 generated["581"]["s_exit"])
    # غير المشمول بالخريطة لا يُمسّ (عيار المخاطرة مثلًا).
    assert hp.dial_override("RISK_DIAL") is None

    # يد المالك بعد التفعيل تعلو المولَّد — لهذا العيار وحده.
    _approve("DECISION_NEUTRAL_BAND", 0.07, at=200.0)
    assert hp.dial_override("DECISION_NEUTRAL_BAND") is None
    assert effective_value("DECISION_NEUTRAL_BAND", 0.05) == 0.07
    assert effective_value("ANALYSIS_FAST_WEIGHT", 55.0) == generated["166"]["fast_weight"]

    # الإطفاء بكبسة: ACTIVE=0 يعيد كل شيء للمعتمد/المانيفست.
    _approve(hp.ACTIVE_NAME, 0.0, at=300.0)
    assert hp.profile_active() is False
    assert effective_value("ANALYSIS_FAST_WEIGHT", 55.0) == 55.0
    assert effective_value("DECISION_NEUTRAL_BAND", 0.05) == 0.07
    assert hp.hysteresis_override(0.20, 0.15) == (0.20, 0.15)
