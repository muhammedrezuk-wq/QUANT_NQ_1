# -*- coding: utf-8 -*-
"""محرك شخصية الأفق (الظل) — معادلات ورقة المؤشر الموحد v1.0 حرفيًّا.

التحويل المعلَن: x = (100 − السرعة)/99 (السرعة 100 = أسرع = x=0).
فحوص الورقة نفسها: أطراف المجال (§9–§41) · الرتابة (§66) · الحدود (§67) ·
قاعدة EMA (§16) · إعادة تطبيع أوزان المسار السريع (§20–§21) · حارس §53 ·
المصدر §55 · منحنى E/H ثابت (§42).
"""
import pytest

from shared import horizon_profile as hp


def test_fastest_endpoint_speed_100():
    p = hp.generate(100.0)
    assert p["x"] == 0.0 and p["profile_index"] == 1.0
    assert p["horizon_seconds"] == 0.25
    assert p["166"]["fast_weight"] == 90.0 and p["166"]["slow_weight"] == 10.0
    assert p["166"]["live_stale_after_s"] == 1.5
    assert p["166"]["fast_required_depth"] == 42.0
    assert p["166"]["slow_required_depth"] == 30.0
    assert p["151"] == {"ema_fast": 5, "ema_slow": 12, "slope_lookback": 2}
    assert p["152"]["roc_period"] == 3 and p["152"]["impulse_window"] == 5
    assert p["152"]["persistence_window"] == 8 and p["152"]["persistence_min"] == 0.52
    assert p["153"] == {"atr_window": 14, "baseline_window": 32, "stddev_window": 16}
    assert p["155"] == {"baseline_window": 24, "exp_short": 5, "exp_long": 20}
    assert p["162"]["baseline_window"] == 16 and p["163"]["baseline_window"] == 16
    assert p["163"]["accel_ratio"] == 0.65
    assert p["165"] == {"window": 12, "noisy_max": 0.30, "efficient_min": 0.60}
    assert p["453"] == {"min_participation": 0.30, "min_confidence": 0.60,
                        "context_weight": 0.06}
    assert p["452"]["low_quality_factor"] == 0.50
    assert p["458"] == {"neutral_band": 0.05, "conflict_ratio": 0.50}
    assert p["413"] == {"min_active_weight": 40.0, "confidence_threshold": 60.0,
                        "required_depth": 60.0}
    assert p["455_456"] == {"min_direction": 55.0, "min_strength": 50.0,
                            "min_confidence": 60.0, "min_current_depth": 50.0}
    assert p["523"]["filter_strength"] == 0.30 and p["523"]["mgmt_cadence_s"] == 2.0
    assert p["523"]["stop_min_frac"] == 0.001 and p["523"]["stop_max_frac"] == 0.003
    assert p["581"] == {"s_enter": 0.35, "s_exit": 0.20}


def test_slowest_endpoint_speed_1():
    p = hp.generate(1.0)
    assert p["x"] == 1.0 and p["profile_index"] == 100.0
    assert p["horizon_seconds"] == 15_552_000.0
    assert p["166"]["fast_weight"] == 65.0 and p["166"]["slow_weight"] == 35.0
    assert p["166"]["live_stale_after_s"] == 6.0
    assert p["151"]["ema_fast"] == 24 and p["151"]["ema_slow"] == 55
    assert p["151"]["slope_lookback"] == 5
    assert p["152"]["roc_period"] == 12 and p["152"]["impulse_window"] == 16
    assert p["152"]["persistence_min"] == 0.60
    assert p["153"] == {"atr_window": 30, "baseline_window": 60, "stddev_window": 32}
    assert p["155"] == {"baseline_window": 60, "exp_short": 12, "exp_long": 40}
    assert p["162"]["baseline_window"] == 44 and p["163"]["accel_ratio"] == 0.50
    assert p["165"] == {"window": 30, "noisy_max": 0.40, "efficient_min": 0.50}
    assert p["453"]["min_participation"] == 0.45 and p["453"]["min_confidence"] == 0.45
    assert p["453"]["context_weight"] == 0.025
    assert p["452"]["low_quality_factor"] == 0.32
    assert p["458"] == {"neutral_band": 0.10, "conflict_ratio": 0.60}
    assert p["413"] == {"min_active_weight": 27.0, "confidence_threshold": 50.0,
                        "required_depth": 45.0}
    assert p["455_456"]["min_direction"] == 58.0
    assert p["523"]["filter_strength"] == 0.80 and p["523"]["mgmt_cadence_s"] == 15.0
    assert p["523"]["stop_min_frac"] == 0.005 and p["523"]["stop_max_frac"] == 0.01
    assert p["581"] == {"s_enter": 0.38, "s_exit": 0.25}


def test_monotonicity_full_scan():
    """§66 — لا انعكاس اتجاه في أي معادلة عبر المجال كله (خطوة 0.5)."""
    previous = None
    for half in range(2, 201):
        speed = half / 2.0
        p = hp.generate(speed)
        if previous is not None:
            # السرعة تصعد ⇒ x يهبط ⇒ الأفق والنوافذ لا تطول والإيقاع لا يبطؤ.
            assert p["horizon_seconds"] <= previous["horizon_seconds"]
            assert p["166"]["fast_weight"] >= previous["166"]["fast_weight"]
            assert p["166"]["live_stale_after_s"] <= previous["166"]["live_stale_after_s"]
            assert p["162"]["baseline_window"] <= previous["162"]["baseline_window"]
            assert p["153"]["baseline_window"] <= previous["153"]["baseline_window"]
            assert p["523"]["mgmt_cadence_s"] <= previous["523"]["mgmt_cadence_s"]
            assert p["581"]["s_enter"] <= previous["581"]["s_enter"]
        previous = p


def test_ema_rule_and_fast_weights_renormalized():
    for tenth in range(10, 1001, 7):
        p = hp.generate(tenth / 10.0)
        assert p["151"]["ema_slow"] > p["151"]["ema_fast"] + 2          # §16
        weights = dict(p["fast_path_weights"])
        assert weights.pop("volume") == 0.0                              # §20
        assert abs(sum(weights.values()) - 100.0) < 0.01                 # §21


def test_invalid_index_guard():
    for bad in (0.5, 0.0, -3, 100.01, 500, None, "x"):
        with pytest.raises(ValueError):
            hp.generate(bad)                                             # §53


def test_provenance_and_fixed_curve():
    p = hp.generate(75.0)
    assert p["formula_version"] == "HORIZON_PROFILE_V1"                  # §55
    assert p["engine_version"] == "1.1.0"
    assert p["source_key"] == "TRADING_HORIZON"
    assert p["key_value"] == 75.0 and p["profile_index"] == 26.0
    assert p["exposure_curve"][0] == [0.0, 0.0, 1.0]                     # §42
    assert p["exposure_curve"][-1] == [1.0, 0.50, 0.20]
    assert p["457"] == {"min_strength": 45.0, "min_confidence": 55.0,
                        "min_current_depth": 45.0}                       # §35
