# -*- coding: utf-8 -*-
"""إطار الاستراتيجيات — واجهة موحّدة + استراتيجيات جاهزة للبكتست.

كل استراتيجية ترث من BaseStrategy وتنفّذ:
  - on_tick(tick)    — تُستدعى عند كل تيك جديد
  - on_candle(candle) — تُستدعى عند إغلاق كل شمعة (اختياري)

الاستراتيجية تُصدر إشارات عبر signal_queue (BUY/SELL/CLOSE/FLAT).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from typing import Any

from backtest.models import Candle, Side, Tick


class Signal:
    """إشارة من الاستراتيجية للمحرك."""
    __slots__ = ("action", "side", "price", "timestamp", "reason", "params")

    def __init__(self, action: str, side: Side | None = None,
                 price: float = 0.0, timestamp: float = 0.0,
                 reason: str = "", params: dict[str, Any] | None = None):
        self.action = action      # "OPEN" | "CLOSE" | "FLAT"
        self.side = side          # BUY | SELL (لـ OPEN)
        self.price = price
        self.timestamp = timestamp
        self.reason = reason
        self.params = params or {}


class BaseStrategy(ABC):
    """الواجهة الأساسية لكل استراتيجية."""

    name: str = "base"
    description: str = ""
    default_params: dict[str, Any] = {}

    def __init__(self, params: dict[str, Any] | None = None):
        self.params = {**self.default_params, **(params or {})}
        self.signals: list[Signal] = []

    def emit(self, signal: Signal) -> None:
        self.signals.append(signal)

    def drain_signals(self) -> list[Signal]:
        out = list(self.signals)
        self.signals.clear()
        return out

    @abstractmethod
    def on_tick(self, tick: Tick) -> None:
        """تُستدعى عند كل تيك جديد."""

    def on_candle(self, candle: Candle) -> None:
        """تُستدعى عند إغلاق كل شمعة (اختياري — يمكن تجاوزها)."""

    def reset(self) -> None:
        """إعادة تهيئة الحالة الداخلية (بين جولات البكتست)."""
        self.signals.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# استراتيجيات جاهزة
# ═══════════════════════════════════════════════════════════════════════════════

class MovingAverageCrossover(BaseStrategy):
    """تقاطع متوسطَين متحركَين (SMA).

    - دخول شراء: SMA_sريع يتقاطع فوق SMA_بطيء
    - دخول بيع: SMA_sريع يتقاطع تحت SMA_بطيء
    - خروج: التقاطع المعاكس
    """
    name = "ma_crossover"
    description = "تقاطع متوسطَين متحركَين بسيطَين"
    default_params = {"fast_period": 10, "slow_period": 30}

    def __init__(self, params: dict[str, Any] | None = None):
        super().__init__(params)
        self.fast_period = int(self.params.get("fast_period", 10))
        self.slow_period = int(self.params.get("slow_period", 30))
        self.prices: deque[float] = deque(maxlen=self.slow_period + 1)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self._position: Side | None = None

    def on_tick(self, tick: Tick) -> None:
        self.prices.append(tick.mid)
        if len(self.prices) < self.slow_period:
            return
        prices_list = list(self.prices)
        fast_ma = sum(prices_list[-self.fast_period:]) / self.fast_period
        slow_ma = sum(prices_list[-self.slow_period:]) / self.slow_period

        if self._prev_fast is not None and self._prev_slow is not None:
            # تقاطع صاعد
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                if self._position == Side.SELL:
                    self.emit(Signal("CLOSE", timestamp=tick.timestamp,
                                     price=tick.mid, reason="MA cross up (close sell)"))
                if self._position != Side.BUY:
                    self.emit(Signal("OPEN", side=Side.BUY, timestamp=tick.timestamp,
                                     price=tick.mid, reason="MA cross up"))
                    self._position = Side.BUY
            # تقاطع نازل
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                if self._position == Side.BUY:
                    self.emit(Signal("CLOSE", timestamp=tick.timestamp,
                                     price=tick.mid, reason="MA cross down (close buy)"))
                if self._position != Side.SELL:
                    self.emit(Signal("OPEN", side=Side.SELL, timestamp=tick.timestamp,
                                     price=tick.mid, reason="MA cross down"))
                    self._position = Side.SELL

        self._prev_fast = fast_ma
        self._prev_slow = slow_ma

    def reset(self) -> None:
        super().reset()
        self.prices.clear()
        self._prev_fast = None
        self._prev_slow = None
        self._position = None


class BreakoutStrategy(BaseStrategy):
    """اختراق أعلى/أدنى فترة (Donchian Channel).

    - شراء عند اختراق أعلى سعر لآخر N تيك
    - بيع عند اختراق أدنى سعر لآخر N تيك
    """
    name = "breakout"
    description = "اختراق قناة دونشان (أعلى/أدنى N)"
    default_params = {"lookback": 50, "exit_lookback": 20}

    def __init__(self, params: dict[str, Any] | None = None):
        super().__init__(params)
        self.lookback = int(self.params.get("lookback", 50))
        self.exit_lookback = int(self.params.get("exit_lookback", 20))
        self.prices: deque[float] = deque(maxlen=self.lookback + 1)
        self._position: Side | None = None

    def on_tick(self, tick: Tick) -> None:
        self.prices.append(tick.mid)
        if len(self.prices) < self.lookback:
            return
        prices_list = list(self.prices)
        # أعلى/أدنى الفترة الرئيسية (بدون آخر تيك)
        highest = max(prices_list[-self.lookback:-1])
        lowest = min(prices_list[-self.lookback:-1])

        if tick.mid > highest and self._position != Side.BUY:
            if self._position == Side.SELL:
                self.emit(Signal("CLOSE", timestamp=tick.timestamp,
                                 price=tick.mid, reason="Breakout up (close sell)"))
            self.emit(Signal("OPEN", side=Side.BUY, timestamp=tick.timestamp,
                             price=tick.mid, reason="Breakout up"))
            self._position = Side.BUY
        elif tick.mid < lowest and self._position != Side.SELL:
            if self._position == Side.BUY:
                self.emit(Signal("CLOSE", timestamp=tick.timestamp,
                                 price=tick.mid, reason="Breakout down (close buy)"))
            self.emit(Signal("OPEN", side=Side.SELL, timestamp=tick.timestamp,
                             price=tick.mid, reason="Breakout down"))
            self._position = Side.SELL

    def reset(self) -> None:
        super().reset()
        self.prices.clear()
        self._position = None


class MeanReversionStrategy(BaseStrategy):
    """ارتداد للسعر المتوسط (Bollinger Bands).

    - شراء عندما ينزل السعر تحت الحد السفلي
    - بيع عندما يصعد فوق الحد العلوي
    - خروج عند العودة للمتوسط
    """
    name = "mean_reversion"
    description = "ارتداد للسعر المتوسط (بولينجر باندز)"
    default_params = {"period": 20, "std_dev": 2.0}

    def __init__(self, params: dict[str, Any] | None = None):
        super().__init__(params)
        self.period = int(self.params.get("period", 20))
        self.std_dev = float(self.params.get("std_dev", 2.0))
        self.prices: deque[float] = deque(maxlen=self.period)
        self._position: Side | None = None

    def on_tick(self, tick: Tick) -> None:
        self.prices.append(tick.mid)
        if len(self.prices) < self.period:
            return
        prices_list = list(self.prices)
        mean = sum(prices_list) / self.period
        variance = sum((p - mean) ** 2 for p in prices_list) / self.period
        std = variance ** 0.5
        if std == 0:
            return
        upper = mean + self.std_dev * std
        lower = mean - self.std_dev * std

        if tick.mid < lower and self._position != Side.BUY:
            if self._position == Side.SELL:
                self.emit(Signal("CLOSE", timestamp=tick.timestamp,
                                 price=tick.mid, reason="Below lower band (close sell)"))
            self.emit(Signal("OPEN", side=Side.BUY, timestamp=tick.timestamp,
                             price=tick.mid, reason="Below lower band"))
            self._position = Side.BUY
        elif tick.mid > upper and self._position != Side.SELL:
            if self._position == Side.BUY:
                self.emit(Signal("CLOSE", timestamp=tick.timestamp,
                                 price=tick.mid, reason="Above upper band (close buy)"))
            self.emit(Signal("OPEN", side=Side.SELL, timestamp=tick.timestamp,
                             price=tick.mid, reason="Above upper band"))
            self._position = Side.SELL
        elif self._position == Side.BUY and tick.mid >= mean:
            self.emit(Signal("CLOSE", timestamp=tick.timestamp,
                             price=tick.mid, reason="Return to mean"))
            self._position = None
        elif self._position == Side.SELL and tick.mid <= mean:
            self.emit(Signal("CLOSE", timestamp=tick.timestamp,
                             price=tick.mid, reason="Return to mean"))
            self._position = None

    def reset(self) -> None:
        super().reset()
        self.prices.clear()
        self._position = None


# ═══════════════════════════════════════════════════════════════════════════════
# سجل الاستراتيجيات المتاحة
# ═══════════════════════════════════════════════════════════════════════════════

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "ma_crossover": MovingAverageCrossover,
    "breakout": BreakoutStrategy,
    "mean_reversion": MeanReversionStrategy,
}


def create_strategy(name: str, params: dict[str, Any] | None = None) -> BaseStrategy:
    """إنشاء استراتيجية بالاسم."""
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"استراتيجية غير معروفة: {name}. المتاحة: {list(STRATEGY_REGISTRY)}")
    return cls(params)


def list_strategies() -> list[dict[str, Any]]:
    """قائمة الاستراتيجيات المتاحة مع معاملاتاتها."""
    return [
        {"name": cls.name, "description": cls.description,
         "default_params": cls.default_params}
        for cls in STRATEGY_REGISTRY.values()
    ]
