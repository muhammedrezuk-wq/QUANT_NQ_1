# -*- coding: utf-8 -*-
"""مختبر المؤشرات — 15 مؤشر تقني حقيقي مع إمكانية التشغيل/الإطفاء.

كل مؤشر:
  - يتلقّى سلسلة تيكات أو شموع
  - يحسب قيمته الحقيقية (مش أرقام عشوائية)
  - يصدر إشارة (BUY/SELL/NEUTRAL) مع قوة الإشارة
  - له معاملات قابلة للمعايرة
  - يمكن إطفائه/تشغيله independently
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from backtest.models import Candle, Tick


# ═══════════════════════════════════════════════════════════════════════════════
# الأنواع الأساسية
# ═══════════════════════════════════════════════════════════════════════════════

class SignalDir:
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


@dataclass(slots=True)
class IndicatorSignal:
    """إشارة من مؤشر واحد."""
    name: str
    direction: str  # BUY / SELL / NEUTRAL
    strength: float  # 0.0 → 1.0
    value: float  # القيمة الحالية للمؤشر
    threshold: float = 0.0  # الحد اللي فوقه بتعتبر الإشارة قوية
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_buy(self) -> bool:
        return self.direction == SignalDir.BUY

    @property
    def is_sell(self) -> bool:
        return self.direction == SignalDir.SELL


@dataclass(slots=True)
class CalibrationResult:
    """نتيجة معايرة مؤشر واحد."""
    name: str
    optimal_params: dict[str, float]
    win_rate: float
    avg_return: float
    sharpe: float
    max_drawdown: float
    trades_tested: int
    param_sweep: list[dict[str, Any]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# الواجهة الأساسية
# ═══════════════════════════════════════════════════════════════════════════════

class BaseIndicator(ABC):
    """واجهة كل مؤشر — يرث منها الـ15 مؤشر."""

    name: str = "base"
    display_name: str = "مؤشر"
    category: str = "trend"  # trend, momentum, volatility, volume, overlay
    default_params: dict[str, float] = {}
    description: str = ""

    def __init__(self, params: dict[str, float] | None = None):
        self.params = {**self.default_params, **(params or {})}
        self.enabled = True
        self._values: list[float] = []
        self._last_signal: IndicatorSignal | None = None

    @abstractmethod
    def update(self, candle: Candle) -> float:
        """تحديث المؤشر بشمعة جديدة — يرجع القيمة."""

    @abstractmethod
    def signal(self) -> IndicatorSignal:
        """يصدر الإشارة الحالية."""

    def reset(self) -> None:
        self._values.clear()
        self._last_signal = None

    @property
    def last_value(self) -> float:
        return self._values[-1] if self._values else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# ١. SMA — المتوسط المتحرك البسيط
# ═══════════════════════════════════════════════════════════════════════════════

class SMA(BaseIndicator):
    name = "sma"
    display_name = "المتوسط المتحرك البسيط"
    category = "overlay"
    default_params = {"period": 20}
    description = "متوسط أسعار الإغلاق لآخر N فترة"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self._prices: deque[float] = deque(maxlen=self.period)

    def update(self, candle: Candle) -> float:
        self._prices.append(candle.close)
        if len(self._prices) < self.period:
            val = sum(self._prices) / len(self._prices)
        else:
            val = sum(self._prices) / self.period
        self._values.append(val)
        return val

    def signal(self) -> IndicatorSignal:
        if len(self._prices) < self.period or len(self._values) < 2:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, self.last_value)
        price = self._prices[-1]
        prev = self._values[-2]
        curr = self._values[-1]
        diff = curr - prev
        if price > curr and diff > 0:
            strength = min(abs(diff) / (curr * 0.001 + 1e-10), 1.0)
            return IndicatorSignal(self.name, SignalDir.BUY, strength, curr)
        elif price < curr and diff < 0:
            strength = min(abs(diff) / (curr * 0.001 + 1e-10), 1.0)
            return IndicatorSignal(self.name, SignalDir.SELL, strength, curr)
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, curr)

    def reset(self):
        super().reset()
        self._prices.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# ٢. EMA — المتوسط المتحرك الأسي
# ═══════════════════════════════════════════════════════════════════════════════

class EMA(BaseIndicator):
    name = "ema"
    display_name = "المتوسط المتحرك الأسي"
    category = "overlay"
    default_params = {"period": 20}
    description = "متوسط أسي يعطي وزن أكبر للأسعار الأخيرة"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self._multiplier = 2.0 / (self.period + 1)
        self._ema: float | None = None
        self._count = 0

    def update(self, candle: Candle) -> float:
        self._count += 1
        if self._ema is None:
            self._ema = candle.close
        else:
            self._ema = (candle.close - self._ema) * self._multiplier + self._ema
        self._values.append(self._ema)
        return self._ema

    def signal(self) -> IndicatorSignal:
        if self._count < self.period or len(self._values) < 2:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, self.last_value)
        prev = self._values[-2]
        curr = self._values[-1]
        diff = curr - prev
        if diff > 0:
            strength = min(abs(diff) / (curr * 0.0005 + 1e-10), 1.0)
            return IndicatorSignal(self.name, SignalDir.BUY, strength, curr)
        elif diff < 0:
            strength = min(abs(diff) / (curr * 0.0005 + 1e-10), 1.0)
            return IndicatorSignal(self.name, SignalDir.SELL, strength, curr)
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, curr)

    def reset(self):
        super().reset()
        self._ema = None
        self._count = 0


# ═══════════════════════════════════════════════════════════════════════════════
# ٣. RSI — مؤشر القوة النسبية
# ═══════════════════════════════════════════════════════════════════════════════

class RSI(BaseIndicator):
    name = "rsi"
    display_name = "مؤشر القوة النسبية"
    category = "momentum"
    default_params = {"period": 14, "overbought": 70, "oversold": 30}
    description = "يقيس سرعة واتجاه التغيرات السعرية (0-100)"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self.overbought = float(self.params["overbought"])
        self.oversold = float(self.params["oversold"])
        self._gains: deque[float] = deque(maxlen=self.period)
        self._losses: deque[float] = deque(maxlen=self.period)
        self._prev_close: float | None = None

    def update(self, candle: Candle) -> float:
        if self._prev_close is not None:
            change = candle.close - self._prev_close
            self._gains.append(max(change, 0))
            self._losses.append(max(-change, 0))
        self._prev_close = candle.close
        if len(self._gains) < self.period:
            self._values.append(50.0)
            return 50.0
        avg_gain = sum(self._gains) / self.period
        avg_loss = sum(self._losses) / self.period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        self._values.append(rsi)
        return rsi

    def signal(self) -> IndicatorSignal:
        if not self._values or len(self._gains) < self.period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 50)
        val = self._values[-1]
        if val < self.oversold:
            strength = (self.oversold - val) / self.oversold
            return IndicatorSignal(self.name, SignalDir.BUY, min(strength, 1.0), val,
                                   details={"zone": "oversold"})
        elif val > self.overbought:
            strength = (val - self.overbought) / (100 - self.overbought)
            return IndicatorSignal(self.name, SignalDir.SELL, min(strength, 1.0), val,
                                   details={"zone": "overbought"})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, val)

    def reset(self):
        super().reset()
        self._gains.clear()
        self._losses.clear()
        self._prev_close = None


# ═══════════════════════════════════════════════════════════════════════════════
# ٤. MACD
# ═══════════════════════════════════════════════════════════════════════════════

class MACD(BaseIndicator):
    name = "macd"
    display_name = "ماكد (MACD)"
    category = "momentum"
    default_params = {"fast": 12, "slow": 26, "signal_period": 9}
    description = "تقاطع EMA سريع وبطيء مع خط إشارة"

    def __init__(self, params=None):
        super().__init__(params)
        self.fast_p = int(self.params["fast"])
        self.slow_p = int(self.params["slow"])
        self.sig_p = int(self.params["signal_period"])
        self._fast_ema: float | None = None
        self._slow_ema: float | None = None
        self._fast_mult = 2.0 / (self.fast_p + 1)
        self._slow_mult = 2.0 / (self.slow_p + 1)
        self._macd_line: deque[float] = deque(maxlen=self.sig_p + 1)
        self._sig_line: float | None = None
        self._sig_mult = 2.0 / (self.sig_p + 1)
        self._prev_macd: float | None = None
        self._histogram: list[float] = []
        self._count = 0

    def update(self, candle: Candle) -> float:
        self._count += 1
        if self._fast_ema is None:
            self._fast_ema = candle.close
            self._slow_ema = candle.close
        else:
            self._fast_ema = (candle.close - self._fast_ema) * self._fast_mult + self._fast_ema
            self._slow_ema = (candle.close - self._slow_ema) * self._slow_mult + self._slow_ema
        macd_val = self._fast_ema - self._slow_ema
        self._macd_line.append(macd_val)
        if self._sig_line is None:
            self._sig_line = macd_val
        else:
            self._sig_line = (macd_val - self._sig_line) * self._sig_mult + self._sig_line
        histogram = macd_val - self._sig_line
        self._histogram.append(histogram)
        self._prev_macd = macd_val
        self._values.append(histogram)
        return histogram

    def signal(self) -> IndicatorSignal:
        if len(self._macd_line) < 2 or len(self._histogram) < 2:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0)
        h_now = self._histogram[-1]
        h_prev = self._histogram[-2]
        if h_prev < 0 and h_now > 0:
            strength = min(abs(h_now) / (abs(h_now) + 1e-10), 1.0)
            return IndicatorSignal(self.name, SignalDir.BUY, strength, h_now,
                                   details={"cross": "bullish"})
        elif h_prev > 0 and h_now < 0:
            strength = min(abs(h_now) / (abs(h_now) + 1e-10), 1.0)
            return IndicatorSignal(self.name, SignalDir.SELL, strength, h_now,
                                   details={"cross": "bearish"})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, h_now)

    def reset(self):
        super().reset()
        self._fast_ema = None
        self._slow_ema = None
        self._macd_line.clear()
        self._sig_line = None
        self._histogram.clear()
        self._count = 0


# ═══════════════════════════════════════════════════════════════════════════════
# ٥. Bollinger Bands
# ═══════════════════════════════════════════════════════════════════════════════

class BollingerBands(BaseIndicator):
    name = "bollinger"
    display_name = "بولينجر باندز"
    category = "volatility"
    default_params = {"period": 20, "std_dev": 2.0}
    description = "نطاق التقلب حول المتوسط — اختراق الحد = إشارة قوية"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self.std_dev = float(self.params["std_dev"])
        self._prices: deque[float] = deque(maxlen=self.period)
        self._upper: float = 0
        self._lower: float = 0
        self._middle: float = 0
        self._pct_b: float = 0.5  # %B position

    def update(self, candle: Candle) -> float:
        self._prices.append(candle.close)
        if len(self._prices) < self.period:
            self._values.append(0)
            return 0
        prices = list(self._prices)
        self._middle = sum(prices) / self.period
        variance = sum((p - self._middle) ** 2 for p in prices) / self.period
        std = math.sqrt(variance) if variance > 0 else 0
        self._upper = self._middle + self.std_dev * std
        self._lower = self._middle - self.std_dev * std
        band_width = self._upper - self._lower
        if band_width > 0:
            self._pct_b = (candle.close - self._lower) / band_width
        else:
            self._pct_b = 0.5
        self._values.append(self._pct_b)
        return self._pct_b

    def signal(self) -> IndicatorSignal:
        if len(self._prices) < self.period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0.5)
        if self._pct_b < 0:
            strength = min(abs(self._pct_b), 1.0)
            return IndicatorSignal(self.name, SignalDir.BUY, strength, self._pct_b,
                                   details={"lower": self._lower, "upper": self._upper})
        elif self._pct_b > 1:
            strength = min(self._pct_b - 1, 1.0)
            return IndicatorSignal(self.name, SignalDir.SELL, strength, self._pct_b,
                                   details={"lower": self._lower, "upper": self._upper})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, self._pct_b)

    def reset(self):
        super().reset()
        self._prices.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# ٦. Stochastic Oscillator
# ═══════════════════════════════════════════════════════════════════════════════

class Stochastic(BaseIndicator):
    name = "stochastic"
    display_name = "ستوكاستك"
    category = "momentum"
    default_params = {"k_period": 14, "d_period": 3, "overbought": 80, "oversold": 20}
    description = "يقارن سعر الإغلاق بنطاق الأسعار لفترة معينة"

    def __init__(self, params=None):
        super().__init__(params)
        self.k_period = int(self.params["k_period"])
        self.d_period = int(self.params["d_period"])
        self.overbought = float(self.params["overbought"])
        self.oversold = float(self.params["oversold"])
        self._closes: deque[float] = deque(maxlen=self.k_period)
        self._highs: deque[float] = deque(maxlen=self.k_period)
        self._lows: deque[float] = deque(maxlen=self.k_period)
        self._k_values: deque[float] = deque(maxlen=self.d_period)
        self._d_value: float = 50

    def update(self, candle: Candle) -> float:
        self._closes.append(candle.close)
        self._highs.append(candle.high)
        self._lows.append(candle.low)
        if len(self._closes) < self.k_period:
            self._values.append(50)
            return 50
        highest = max(self._highs)
        lowest = min(self._lows)
        rng = highest - lowest
        if rng > 0:
            k = ((candle.close - lowest) / rng) * 100
        else:
            k = 50
        self._k_values.append(k)
        self._d_value = sum(self._k_values) / len(self._k_values)
        self._values.append(k)
        return k

    def signal(self) -> IndicatorSignal:
        if len(self._closes) < self.k_period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 50)
        k = self._values[-1]
        if k < self.oversold:
            strength = (self.oversold - k) / self.oversold
            return IndicatorSignal(self.name, SignalDir.BUY, min(strength, 1.0), k,
                                   details={"k": k, "d": self._d_value})
        elif k > self.overbought:
            strength = (k - self.overbought) / (100 - self.overbought)
            return IndicatorSignal(self.name, SignalDir.SELL, min(strength, 1.0), k,
                                   details={"k": k, "d": self._d_value})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, k)

    def reset(self):
        super().reset()
        self._closes.clear()
        self._highs.clear()
        self._lows.clear()
        self._k_values.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# ٧. ATR — متوسط المدى الحقيقي
# ═══════════════════════════════════════════════════════════════════════════════

class ATR(BaseIndicator):
    name = "atr"
    display_name = "متوسط المدى الحقيقي"
    category = "volatility"
    default_params = {"period": 14}
    description = "يقيس التقلب — مفيد لتحديد وقف الخسارة وحجم الصفقة"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self._tr_values: deque[float] = deque(maxlen=self.period)
        self._prev_close: float | None = None

    def update(self, candle: Candle) -> float:
        if self._prev_close is not None:
            tr = max(
                candle.high - candle.low,
                abs(candle.high - self._prev_close),
                abs(candle.low - self._prev_close),
            )
        else:
            tr = candle.high - candle.low
        self._tr_values.append(tr)
        self._prev_close = candle.close
        atr = sum(self._tr_values) / len(self._tr_values)
        self._values.append(atr)
        return atr

    def signal(self) -> IndicatorSignal:
        if len(self._tr_values) < self.period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, self.last_value)
        atr = self.last_value
        # ATR بحد ذاته ما يعطي اتجاه — بس يُستخدم لتحديد القوة
        avg_atr = sum(self._values[-self.period * 2:]) / min(len(self._values), self.period * 2) if len(self._values) > 1 else atr
        ratio = atr / avg_atr if avg_atr > 0 else 1
        if ratio > 1.5:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, min(ratio / 3, 1.0), atr,
                                   details={"volatility": "high"})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, atr)

    def reset(self):
        super().reset()
        self._tr_values.clear()
        self._prev_close = None


# ═══════════════════════════════════════════════════════════════════════════════
# ٨. ADX — مؤشر الاتجاه
# ═══════════════════════════════════════════════════════════════════════════════

class ADX(BaseIndicator):
    name = "adx"
    display_name = "مؤشر الاتجاه (ADX)"
    category = "trend"
    default_params = {"period": 14, "strong_trend": 25}
    description = "يقيس قوة الاتجاه (مش اتجاهه) — فوق 25 = اتجاه قوي"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self.strong_trend = float(self.params["strong_trend"])
        self._tr: deque[float] = deque(maxlen=self.period)
        self._plus_dm: deque[float] = deque(maxlen=self.period)
        self._minus_dm: deque[float] = deque(maxlen=self.period)
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_close: float | None = None
        self._adx: float = 0

    def update(self, candle: Candle) -> float:
        if self._prev_high is not None:
            tr = max(
                candle.high - candle.low,
                abs(candle.high - self._prev_close),
                abs(candle.low - self._prev_close),
            )
            up_move = candle.high - self._prev_high
            down_move = self._prev_low - candle.low
            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0
            self._tr.append(tr)
            self._plus_dm.append(plus_dm)
            self._minus_dm.append(minus_dm)
        self._prev_high = candle.high
        self._prev_low = candle.low
        self._prev_close = candle.close
        if len(self._tr) < self.period:
            self._values.append(0)
            return 0
        atr = sum(self._tr) / self.period
        plus_di = (sum(self._plus_dm) / self.period / atr * 100) if atr > 0 else 0
        minus_di = (sum(self._minus_dm) / self.period / atr * 100) if atr > 0 else 0
        di_sum = plus_di + minus_di
        dx = (abs(plus_di - minus_di) / di_sum * 100) if di_sum > 0 else 0
        self._adx = dx  # مبسّط — النسخة الكاملة تستخدم smoothing
        self._values.append(self._adx)
        return self._adx

    def signal(self) -> IndicatorSignal:
        if len(self._tr) < self.period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0)
        if self._adx >= self.strong_trend:
            plus_di = (sum(self._plus_dm) / self.period)
            minus_di = (sum(self._minus_dm) / self.period)
            if plus_di > minus_di:
                strength = min((self._adx - self.strong_trend) / 50, 1.0)
                return IndicatorSignal(self.name, SignalDir.BUY, strength, self._adx,
                                       details={"trend_strength": self._adx})
            else:
                strength = min((self._adx - self.strong_trend) / 50, 1.0)
                return IndicatorSignal(self.name, SignalDir.SELL, strength, self._adx,
                                       details={"trend_strength": self._adx})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, self._adx,
                               details={"trend_strength": "weak"})

    def reset(self):
        super().reset()
        self._tr.clear()
        self._plus_dm.clear()
        self._minus_dm.clear()
        self._prev_high = None
        self._prev_low = None
        self._prev_close = None
        self._adx = 0


# ═══════════════════════════════════════════════════════════════════════════════
# ٩. CCI — مؤشر القناة السلعية
# ═══════════════════════════════════════════════════════════════════════════════

class CCI(BaseIndicator):
    name = "cci"
    display_name = "مؤشر القناة السلعية"
    category = "momentum"
    default_params = {"period": 20, "overbought": 100, "oversold": -100}
    description = "يقيس انحراف السعر عن متوسطه الإحصائي"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self.overbought = float(self.params["overbought"])
        self.oversold = float(self.params["oversold"])
        self._typical: deque[float] = deque(maxlen=self.period)

    def update(self, candle: Candle) -> float:
        tp = (candle.high + candle.low + candle.close) / 3
        self._typical.append(tp)
        if len(self._typical) < self.period:
            self._values.append(0)
            return 0
        tps = list(self._typical)
        mean_tp = sum(tps) / self.period
        mean_dev = sum(abs(t - mean_tp) for t in tps) / self.period
        cci = (tp - mean_tp) / (0.015 * mean_dev) if mean_dev > 0 else 0
        self._values.append(cci)
        return cci

    def signal(self) -> IndicatorSignal:
        if len(self._typical) < self.period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0)
        val = self._values[-1]
        if val < self.oversold:
            strength = min(abs(val - self.oversold) / 200, 1.0)
            return IndicatorSignal(self.name, SignalDir.BUY, strength, val)
        elif val > self.overbought:
            strength = min(abs(val - self.overbought) / 200, 1.0)
            return IndicatorSignal(self.name, SignalDir.SELL, strength, val)
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, val)

    def reset(self):
        super().reset()
        self._typical.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# ١٠. Williams %R
# ═══════════════════════════════════════════════════════════════════════════════

class WilliamsR(BaseIndicator):
    name = "williams_r"
    display_name = "وليامز %R"
    category = "momentum"
    default_params = {"period": 14, "overbought": -20, "oversold": -80}
    description = "مؤشر زخم يشبه ستوكاستك — من -100 إلى 0"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self.overbought = float(self.params["overbought"])
        self.oversold = float(self.params["oversold"])
        self._closes: deque[float] = deque(maxlen=self.period)
        self._highs: deque[float] = deque(maxlen=self.period)
        self._lows: deque[float] = deque(maxlen=self.period)

    def update(self, candle: Candle) -> float:
        self._closes.append(candle.close)
        self._highs.append(candle.high)
        self._lows.append(candle.low)
        if len(self._closes) < self.period:
            self._values.append(-50)
            return -50
        highest = max(self._highs)
        lowest = min(self._lows)
        rng = highest - lowest
        wr = ((highest - candle.close) / rng * -100) if rng > 0 else -50
        self._values.append(wr)
        return wr

    def signal(self) -> IndicatorSignal:
        if len(self._closes) < self.period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, -50)
        val = self._values[-1]
        if val < self.oversold:
            strength = (self.oversold - val) / (100 + self.oversold)
            return IndicatorSignal(self.name, SignalDir.BUY, min(abs(strength), 1.0), val)
        elif val > self.overbought:
            strength = (val - self.overbought) / (100 - self.overbought)
            return IndicatorSignal(self.name, SignalDir.SELL, min(abs(strength), 1.0), val)
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, val)

    def reset(self):
        super().reset()
        self._closes.clear()
        self._highs.clear()
        self._lows.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# ١١. Volume Oscillator
# ═══════════════════════════════════════════════════════════════════════════════

class VolumeOscillator(BaseIndicator):
    name = "volume_osc"
    display_name = "مذبذب الحجم"
    category = "volume"
    default_params = {"fast": 5, "slow": 20}
    description = "الفرق بين متوسطَي حجم قصير وطويل — يؤكد الاتجاه"

    def __init__(self, params=None):
        super().__init__(params)
        self.fast = int(self.params["fast"])
        self.slow = int(self.params["slow"])
        self._volumes: deque[float] = deque(maxlen=self.slow)

    def update(self, candle: Candle) -> float:
        self._volumes.append(candle.volume)
        if len(self._volumes) < self.slow:
            self._values.append(0)
            return 0
        vols = list(self._volumes)
        fast_avg = sum(vols[-self.fast:]) / self.fast
        slow_avg = sum(vols) / self.slow
        osc = ((fast_avg - slow_avg) / slow_avg * 100) if slow_avg > 0 else 0
        self._values.append(osc)
        return osc

    def signal(self) -> IndicatorSignal:
        if len(self._volumes) < self.slow:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0)
        val = self._values[-1]
        if val > 50:
            return IndicatorSignal(self.name, SignalDir.BUY, min(val / 200, 1.0), val,
                                   details={"volume_confirm": True})
        elif val < -50:
            return IndicatorSignal(self.name, SignalDir.SELL, min(abs(val) / 200, 1.0), val,
                                   details={"volume_confirm": True})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, val)

    def reset(self):
        super().reset()
        self._volumes.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# ١٢. Pivot Points
# ═══════════════════════════════════════════════════════════════════════════════

class PivotPoints(BaseIndicator):
    name = "pivot"
    display_name = "نقاط البيفوت"
    category = "overlay"
    default_params = {}
    description = "مستويات دعم ومقاومة من الشمعة السابقة"

    def __init__(self, params=None):
        super().__init__(params)
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_close: float | None = None
        self._pivot: float = 0
        self._r1: float = 0
        self._s1: float = 0

    def update(self, candle: Candle) -> float:
        if self._prev_high is not None:
            self._pivot = (self._prev_high + self._prev_low + self._prev_close) / 3
            self._r1 = 2 * self._pivot - self._prev_low
            self._s1 = 2 * self._pivot - self._prev_high
        self._prev_high = candle.high
        self._prev_low = candle.low
        self._prev_close = candle.close
        self._values.append(self._pivot)
        return self._pivot

    def signal(self) -> IndicatorSignal:
        if self._pivot == 0:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0)
        # نقارن آخر سعر بالبيفوت
        # (نحتاج آخر close — مخزّن بـ _prev_close بعد آخر update)
        if self._prev_close is not None:
            if self._prev_close > self._r1:
                return IndicatorSignal(self.name, SignalDir.BUY, 0.7, self._pivot,
                                       details={"R1": self._r1, "S1": self._s1, "P": self._pivot})
            elif self._prev_close < self._s1:
                return IndicatorSignal(self.name, SignalDir.SELL, 0.7, self._pivot,
                                       details={"R1": self._r1, "S1": self._s1, "P": self._pivot})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, self._pivot,
                               details={"R1": self._r1, "S1": self._s1, "P": self._pivot})

    def reset(self):
        super().reset()
        self._prev_high = None
        self._prev_low = None
        self._prev_close = None


# ═══════════════════════════════════════════════════════════════════════════════
# ١٣. VWAP — متوسط السعر المرجح بالحجم
# ═══════════════════════════════════════════════════════════════════════════════

class VWAP(BaseIndicator):
    name = "vwap"
    display_name = "متوسط السعر المرجح بالحجم"
    category = "overlay"
    default_params = {}
    description = "السعر العادل — فوقه = شراء، تحته = بيع"

    def __init__(self, params=None):
        super().__init__(params)
        self._cum_vol: float = 0
        self._cum_tp_vol: float = 0
        self._vwap: float = 0

    def update(self, candle: Candle) -> float:
        tp = (candle.high + candle.low + candle.close) / 3
        self._cum_vol += candle.volume
        self._cum_tp_vol += tp * candle.volume
        self._vwap = self._cum_tp_vol / self._cum_vol if self._cum_vol > 0 else tp
        self._values.append(self._vwap)
        return self._vwap

    def signal(self) -> IndicatorSignal:
        if not self._values:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0)
        # نحتاج آخر close — نحسبه من آخر قيمة
        # نستخدم _vwap الحالي ونقارنه بالسعر
        # نرجع إشارة محايدة لأنه يحتاج سياق إضافي
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, self._vwap)

    def reset(self):
        super().reset()
        self._cum_vol = 0
        self._cum_tp_vol = 0
        self._vwap = 0


# ═══════════════════════════════════════════════════════════════════════════════
# ١٤. Momentum
# ═══════════════════════════════════════════════════════════════════════════════

class Momentum(BaseIndicator):
    name = "momentum"
    display_name = "الزخم"
    category = "momentum"
    default_params = {"period": 10}
    description = "الفرق بين السعر الحالي وسعر قبل N فترة"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self._closes: deque[float] = deque(maxlen=self.period + 1)

    def update(self, candle: Candle) -> float:
        self._closes.append(candle.close)
        if len(self._closes) <= self.period:
            self._values.append(0)
            return 0
        mom = candle.close - self._closes[0]
        self._values.append(mom)
        return mom

    def signal(self) -> IndicatorSignal:
        if len(self._closes) <= self.period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0)
        val = self._values[-1]
        if len(self._values) >= 2:
            prev = self._values[-2]
            if prev < 0 and val > 0:
                return IndicatorSignal(self.name, SignalDir.BUY, min(abs(val) / 0.001, 1.0), val)
            elif prev > 0 and val < 0:
                return IndicatorSignal(self.name, SignalDir.SELL, min(abs(val) / 0.001, 1.0), val)
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, val)

    def reset(self):
        super().reset()
        self._closes.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# ١٥. Envelope / Channel
# ═══════════════════════════════════════════════════════════════════════════════

class Envelope(BaseIndicator):
    name = "envelope"
    display_name = "القناة (Envelope)"
    category = "overlay"
    default_params = {"period": 20, "deviation": 0.025}
    description = "قناة حول SMA بنسبة ثابتة — اختراق = انعكاس محتمل"

    def __init__(self, params=None):
        super().__init__(params)
        self.period = int(self.params["period"])
        self.deviation = float(self.params["deviation"])
        self._prices: deque[float] = deque(maxlen=self.period)
        self._sma: float = 0
        self._upper: float = 0
        self._lower: float = 0

    def update(self, candle: Candle) -> float:
        self._prices.append(candle.close)
        if len(self._prices) < self.period:
            self._values.append(0)
            return 0
        self._sma = sum(self._prices) / self.period
        self._upper = self._sma * (1 + self.deviation)
        self._lower = self._sma * (1 - self.deviation)
        pos = (candle.close - self._lower) / (self._upper - self._lower) if (self._upper - self._lower) > 0 else 0.5
        self._values.append(pos)
        return pos

    def signal(self) -> IndicatorSignal:
        if len(self._prices) < self.period:
            return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, 0.5)
        val = self._values[-1]
        if val < 0:
            return IndicatorSignal(self.name, SignalDir.BUY, min(abs(val), 1.0), val,
                                   details={"upper": self._upper, "lower": self._lower})
        elif val > 1:
            return IndicatorSignal(self.name, SignalDir.SELL, min(val - 1, 1.0), val,
                                   details={"upper": self._upper, "lower": self._lower})
        return IndicatorSignal(self.name, SignalDir.NEUTRAL, 0, val)

    def reset(self):
        super().reset()
        self._prices.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# السجل — كل المؤشرات
# ═══════════════════════════════════════════════════════════════════════════════

INDICATOR_REGISTRY: dict[str, type[BaseIndicator]] = {
    "sma": SMA,
    "ema": EMA,
    "rsi": RSI,
    "macd": MACD,
    "bollinger": BollingerBands,
    "stochastic": Stochastic,
    "atr": ATR,
    "adx": ADX,
    "cci": CCI,
    "williams_r": WilliamsR,
    "volume_osc": VolumeOscillator,
    "pivot": PivotPoints,
    "vwap": VWAP,
    "momentum": Momentum,
    "envelope": Envelope,
}


def create_indicator(name: str, params: dict[str, float] | None = None) -> BaseIndicator:
    """إنشاء مؤشر بالاسم."""
    cls = INDICATOR_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"مؤشر غير معروف: {name}. المتاحة: {list(INDICATOR_REGISTRY)}")
    return cls(params)


def list_indicators() -> list[dict[str, Any]]:
    """قائمة كل المؤشرات."""
    return [
        {"name": cls.name, "display_name": cls.display_name,
         "category": cls.category, "description": cls.description,
         "default_params": cls.default_params}
        for cls in INDICATOR_REGISTRY.values()
    ]
