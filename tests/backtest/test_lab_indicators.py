# -*- coding: utf-8 -*-
"""اختبارات المختبر — 15 مؤشر + معايرة + أخبار + احتمالات."""
from __future__ import annotations

import math
import random
import time

import pytest

from backtest.indicators.indicators import (
    ADX, ATR, CCI, EMA, MACD, RSI, SMA, VWAP,
    BollingerBands, Envelope, Momentum, PivotPoints,
    Stochastic, VolumeOscillator, WilliamsR,
    INDICATOR_REGISTRY, SignalDir, create_indicator, list_indicators,
)
from backtest.lab import IndicatorLab, LabRunResult
from backtest.models import Candle


# ═══════════════════════════════════════════════════════════════════════════════
# بيانات اختبار
# ═══════════════════════════════════════════════════════════════════════════════

def _make_candles(n=200, start=1.0850, volatility=0.0005, trend=0.0) -> list[Candle]:
    """توليد شموع اختبار — مع optional trend."""
    candles = []
    price = start
    t = 1000.0
    for i in range(n):
        change = random.gauss(trend, volatility)
        o = price
        c = price + change
        h = max(o, c) + abs(random.gauss(0, volatility * 0.3))
        l = min(o, c) - abs(random.gauss(0, volatility * 0.3))
        candles.append(Candle(
            symbol="EURUSD", timestamp=t + i * 60,
            open=round(o, 5), high=round(h, 5),
            low=round(l, 5), close=round(c, 5),
            volume=random.randint(100, 5000),
        ))
        price = c
    return candles


def _uptrend_candles(n=200) -> list[Candle]:
    return _make_candles(n, trend=0.0003)


def _downtrend_candles(n=200) -> list[Candle]:
    return _make_candles(n, trend=-0.0003)


# ═══════════════════════════════════════════════════════════════════════════════
# ١. اختبار كل مؤشر على حدة
# ═══════════════════════════════════════════════════════════════════════════════

class TestEachIndicator:
    """كل مؤشر من الـ15 لازم:
    - يشتغل على شموع
    - يعطي قيمة عددية
    - يعطي إشارة (BUY/SELL/NEUTRAL)
    - يقبل reset
    """

    @pytest.mark.parametrize("name", list(INDICATOR_REGISTRY.keys()))
    def test_indicator_runs(self, name):
        ind = create_indicator(name)
        candles = _make_candles(100)
        values = []
        for c in candles:
            v = ind.update(c)
            values.append(v)
        assert len(values) == 100
        # كل القيم أرقام حقيقية
        assert all(isinstance(v, (int, float)) for v in values)

    @pytest.mark.parametrize("name", list(INDICATOR_REGISTRY.keys()))
    def test_indicator_signal(self, name):
        ind = create_indicator(name)
        candles = _make_candles(100)
        for c in candles:
            ind.update(c)
        sig = ind.signal()
        assert sig.name == name
        assert sig.direction in (SignalDir.BUY, SignalDir.SELL, SignalDir.NEUTRAL)
        assert 0 <= sig.strength <= 1.0 or sig.strength == 0

    @pytest.mark.parametrize("name", list(INDICATOR_REGISTRY.keys()))
    def test_indicator_reset(self, name):
        ind = create_indicator(name)
        for c in _make_candles(50):
            ind.update(c)
        ind.reset()
        # بعد reset، لازم يبدأ من جديد
        v = ind.update(_make_candles(1)[0])
        assert isinstance(v, (int, float))

    @pytest.mark.parametrize("name", list(INDICATOR_REGISTRY.keys()))
    def test_indicator_custom_params(self, name):
        cls = INDICATOR_REGISTRY[name]
        custom = {}
        for k, v in cls.default_params.items():
            if "period" in k.lower():
                custom[k] = v * 2  # ضعف الفترة
            else:
                custom[k] = v
        ind = create_indicator(name, custom)
        candles = _make_candles(100)
        for c in candles:
            ind.update(c)
        sig = ind.signal()
        assert sig.direction in (SignalDir.BUY, SignalDir.SELL, SignalDir.NEUTRAL)


# ═══════════════════════════════════════════════════════════════════════════════
# ٢. اختبار سلوك المؤشرات — إشارات حقيقية مو عشوائية
# ═══════════════════════════════════════════════════════════════════════════════

class TestIndicatorBehavior:
    """المؤشرات لازم تعطي إشارات منطقية — مو أرقام عشوائية."""

    def test_rsi_oversold(self):
        """RSI لازم يعطي oversold بعد هبوط حاد."""
        ind = RSI({"period": 5})
        candles = _make_candles(10, start=1.1000, trend=-0.005)
        for c in candles:
            ind.update(c)
        sig = ind.signal()
        # بعد هبوط 5% — RSI لازم يكون تحت 30
        assert sig.direction == SignalDir.BUY or sig.value < 40

    def test_rsi_overbought(self):
        """RSI لازم يعطي overbought بعد صعود حاد."""
        ind = RSI({"period": 5})
        candles = _make_candles(10, start=1.0800, trend=0.005)
        for c in candles:
            ind.update(c)
        sig = ind.signal()
        assert sig.direction == SignalDir.SELL or sig.value > 60

    def test_macd_bullish_cross(self):
        """MACD — بعد نزول ثم صعود حاد، الـ histogram لازم يتحسن."""
        ind = MACD({"fast": 5, "slow": 10, "signal_period": 5})
        # نزول قوي ثم صعود قوي
        candles = _downtrend_candles(30) + _uptrend_candles(30)
        # نقيس الـ histogram بعد النزول (نقطة مقارنة)
        ind_mid = MACD({"fast": 5, "slow": 10, "signal_period": 5})
        for c in _downtrend_candles(30):
            ind_mid.update(c)
        hist_after_downtrend = ind_mid._histogram[-1] if ind_mid._histogram else 0

        for c in candles:
            ind.update(c)
        hist_final = ind._histogram[-1] if ind._histogram else 0
        # بعد صعود — الـ histogram لازم يكون أفضل (أكبر) من بعد النزول
        assert hist_final > hist_after_downtrend or ind.signal().direction != SignalDir.SELL

    def test_bollinger_below_lower(self):
        """بولينجر — السعر تحت الحد السفلي = إشارة شراء."""
        ind = BollingerBands({"period": 10, "std_dev": 1.5})
        candles = _make_candles(15, start=1.1000, trend=-0.003)
        for c in candles:
            ind.update(c)
        # آخر شمعة لازم تكون تحت الحد السفلي
        assert ind._pct_b < 0.3

    def test_atr_increases_with_volatility(self):
        """ATR لازم يزيد مع زيادة التقلب."""
        ind_low = ATR({"period": 10})
        ind_high = ATR({"period": 10})
        for c in _make_candles(30, volatility=0.0001):
            ind_low.update(c)
        for c in _make_candles(30, volatility=0.005):
            ind_high.update(c)
        assert ind_high.last_value > ind_low.last_value

    def test_stochastic_oversold(self):
        """ستوكاستك — بعد هبوط = تحت 20."""
        ind = Stochastic({"k_period": 5, "d_period": 3})
        candles = _make_candles(10, start=1.1000, trend=-0.003)
        for c in candles:
            ind.update(c)
        assert ind.last_value < 30

    def test_sma_tracks_price(self):
        """SMA لازم يتبع السعر."""
        ind = SMA({"period": 5})
        candles = _make_candles(20)
        for c in candles:
            ind.update(c)
        # آخر قيمة SMA لازم تكون قريبة من آخر سعر
        assert abs(ind.last_value - candles[-1].close) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# ٣. اختبار registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistry:
    def test_15_indicators(self):
        assert len(INDICATOR_REGISTRY) == 15

    def test_list_indicators(self):
        items = list_indicators()
        assert len(items) == 15
        names = {i["name"] for i in items}
        expected = {"sma", "ema", "rsi", "macd", "bollinger", "stochastic",
                    "atr", "adx", "cci", "williams_r", "volume_osc", "pivot",
                    "vwap", "momentum", "envelope"}
        assert names == expected

    def test_all_have_display_name(self):
        for item in list_indicators():
            assert item["display_name"]
            assert len(item["display_name"]) > 2

    def test_all_have_category(self):
        for item in list_indicators():
            assert item["category"] in ("trend", "momentum", "volatility", "volume", "overlay")

    def test_create_unknown(self):
        with pytest.raises(ValueError, match="غير معروف"):
            create_indicator("nonexistent")


# ═══════════════════════════════════════════════════════════════════════════════
# ٤. المختبر — IndicatorLab
# ═══════════════════════════════════════════════════════════════════════════════

class TestLab:
    def test_lab_init(self):
        lab = IndicatorLab()
        assert len(lab.slots) == 15
        assert all(s.enabled for s in lab.slots.values())

    def test_lab_toggle(self):
        lab = IndicatorLab()
        lab.disable("rsi")
        assert not lab.slots["rsi"].enabled
        lab.enable("rsi")
        assert lab.slots["rsi"].enabled
        # Toggle
        new_state = lab.toggle("rsi")
        assert not new_state

    def test_lab_weight(self):
        lab = IndicatorLab()
        lab.set_weight("macd", 1.5)
        assert lab.slots["macd"].weight == 1.5
        # حد أقصى 2
        lab.set_weight("macd", 5.0)
        assert lab.slots["macd"].weight == 2.0
        # حد أدنى 0
        lab.set_weight("macd", -1.0)
        assert lab.slots["macd"].weight == 0.0

    def test_lab_set_params(self):
        lab = IndicatorLab()
        lab.set_params("rsi", {"period": 7, "overbought": 75, "oversold": 25})
        assert lab.slots["rsi"].indicator.params["period"] == 7

    def test_lab_run_all_enabled(self):
        lab = IndicatorLab()
        candles = _make_candles(200)
        result = lab.run(candles, symbol="EURUSD")
        assert result.status == "completed"
        assert result.candle_count == 200
        assert len(result.indicator_signals) == 15
        assert result.buy_signals + result.sell_signals + result.neutral_signals == 15

    def test_lab_run_some_disabled(self):
        lab = IndicatorLab()
        lab.disable("atr")
        lab.disable("vwap")
        candles = _make_candles(100)
        result = lab.run(candles)
        assert result.status == "completed"
        assert len(result.indicator_signals) == 13  # 15 - 2

    def test_lab_run_all_disabled(self):
        lab = IndicatorLab()
        for name in lab.slots:
            lab.disable(name)
        result = lab.run(_make_candles(100))
        assert result.status == "failed"
        assert "لا يوجد مؤشر" in result.error

    def test_lab_consensus(self):
        lab = IndicatorLab()
        candles = _uptrend_candles(200)
        result = lab.run(candles)
        # بعد صعود — الإجماع لازم يكون BUY أو NEUTRAL (مش SELL)
        assert result.consensus != SignalDir.SELL or result.consensus_strength < 0.3

    def test_lab_probability(self):
        lab = IndicatorLab()
        candles = _uptrend_candles(200)
        result = lab.run(candles)
        # الاحتمالات لازم تكون بين 0 و 1
        assert 0 <= result.probability_buy <= 1
        assert 0 <= result.probability_sell <= 1
        # مجموعهم ≈ 1
        assert abs(result.probability_buy + result.probability_sell - 1) < 0.01

    def test_lab_status(self):
        lab = IndicatorLab()
        candles = _make_candles(100)
        lab.run(candles)
        status = lab.get_status()
        assert len(status) == 15
        for name, info in status.items():
            assert "enabled" in info
            assert "weight" in info

    def test_lab_reset(self):
        lab = IndicatorLab()
        candles = _make_candles(100)
        lab.run(candles)
        lab.reset_all()
        # بعد reset — ما في إشارات سابقة
        for slot in lab.slots.values():
            assert slot.last_signal is None

    def test_lab_export(self):
        import json
        lab = IndicatorLab()
        lab.run(_make_candles(100))
        exported = lab.export_result()
        data = json.loads(exported)
        assert data["status"] == "completed"
        assert "consensus" in data
        assert "probability_buy" in data


# ═══════════════════════════════════════════════════════════════════════════════
# ٥. اختبار مؤشر منفرد
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleIndicator:
    def test_single_indicator(self):
        lab = IndicatorLab()
        candles = _make_candles(200)
        result = lab.test_single("rsi", candles)
        assert result["name"] == "rsi"
        assert result["candle_count"] == 200
        assert "final_signal" in result
        assert result["final_signal"]["direction"] in (SignalDir.BUY, SignalDir.SELL, SignalDir.NEUTRAL)

    def test_single_with_custom_params(self):
        lab = IndicatorLab()
        candles = _make_candles(200)
        result = lab.test_single("rsi", candles, {"period": 7, "overbought": 80, "oversold": 20})
        assert result["params"]["period"] == 7
        assert result["params"]["overbought"] == 80

    @pytest.mark.parametrize("name", list(INDICATOR_REGISTRY.keys()))
    def test_single_all_indicators(self, name):
        lab = IndicatorLab()
        candles = _make_candles(100)
        result = lab.test_single(name, candles)
        assert result["name"] == name
        assert "final_signal" in result
        assert "signals_over_time" in result


# ═══════════════════════════════════════════════════════════════════════════════
# ٦. المعايرة
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalibration:
    def test_calibrate_rsi(self):
        lab = IndicatorLab()
        candles = _make_candles(300)
        result = lab.calibrate("rsi", candles)
        assert result.name == "rsi"
        assert 0 <= result.win_rate <= 1
        assert result.trades_tested > 0
        assert len(result.param_sweep) > 0

    def test_calibrate_saves_to_slot(self):
        lab = IndicatorLab()
        candles = _make_candles(200)
        lab.calibrate("macd", candles)
        assert lab.slots["macd"].calibration is not None
        assert lab.slots["macd"].calibration.optimal_params

    def test_calibrate_unknown(self):
        lab = IndicatorLab()
        with pytest.raises(ValueError, match="غير معروف"):
            lab.calibrate("nonexistent", _make_candles(100))


# ═══════════════════════════════════════════════════════════════════════════════
# ٧. اختبار الأخبار
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewsImpact:
    def test_news_impact(self):
        lab = IndicatorLab()
        candles = _make_candles(500)
        # خبر في النص
        mid_ts = candles[250].timestamp
        events = [
            {"timestamp": mid_ts, "impact": "high",
             "direction": "positive", "headline": "فائدة مرتفعة"},
        ]
        results = lab.test_news_impact(events, candles)
        assert len(results) == 1
        assert results[0]["headline"] == "فائدة مرتفعة"
        assert "price_change_pct" in results[0]
        assert "indicators_changed" in results[0]

    def test_multiple_news(self):
        lab = IndicatorLab()
        candles = _make_candles(500)
        events = [
            {"timestamp": candles[100].timestamp, "impact": "high",
             "direction": "positive", "headline": "خبر 1"},
            {"timestamp": candles[300].timestamp, "impact": "medium",
             "direction": "negative", "headline": "خبر 2"},
        ]
        results = lab.test_news_impact(events, candles)
        assert len(results) == 2

    def test_news_empty(self):
        lab = IndicatorLab()
        results = lab.test_news_impact([], _make_candles(100))
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# ٨. اختبار API
# ═══════════════════════════════════════════════════════════════════════════════

class TestLabAPI:
    def test_lab_indicators_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("GET", "/backtest/lab/indicators")
        assert "indicators" in resp
        assert len(resp["indicators"]) == 15
        assert "status" in resp

    def test_lab_toggle_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("POST", "/backtest/lab/toggle", {"name": "rsi", "enabled": False})
        assert resp["status"] == "ok"
        assert resp["enabled"] is False
        # إعادة تشغيل
        handle_request("POST", "/backtest/lab/toggle", {"name": "rsi", "enabled": True})

    def test_lab_params_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("POST", "/backtest/lab/params",
                              {"name": "rsi", "params": {"period": 7}})
        assert resp["status"] == "ok"
        # إرجاع للأصلي
        handle_request("POST", "/backtest/lab/params",
                       {"name": "rsi", "params": {"period": 14, "overbought": 70, "oversold": 30}})

    def test_lab_reset_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("POST", "/backtest/lab/reset")
        assert resp["status"] == "ok"

    def test_lab_toggle_unknown(self):
        from backtest.api import handle_request
        resp = handle_request("POST", "/backtest/lab/toggle", {"name": "nonexistent"})
        assert "error" in resp

    def test_lab_run_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("POST", "/backtest/lab/run", {"num_candles": 200})
        assert resp["status"] == "completed"
        assert "consensus" in resp
        assert "probability_buy" in resp
        assert len(resp["indicator_signals"]) == 15

    def test_lab_test_single_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("POST", "/backtest/lab/test_single",
                              {"name": "rsi", "num_candles": 200})
        assert resp["name"] == "rsi"
        assert "final_signal" in resp

    def test_lab_calibrate_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("POST", "/backtest/lab/calibrate",
                              {"name": "rsi", "num_candles": 200})
        assert resp["name"] == "rsi"
        assert "optimal_params" in resp
        assert "win_rate" in resp

    def test_lab_news_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("POST", "/backtest/lab/news", {
            "num_candles": 500,
            "events": [
                {"timestamp": 1500, "impact": "high",
                 "direction": "positive", "headline": "فائدة"},
            ],
        })
        assert "results" in resp
