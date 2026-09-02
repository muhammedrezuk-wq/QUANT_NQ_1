# -*- coding: utf-8 -*-
"""مختبر المؤشرات — Lab Controller.

المختبر الحقيقي — مو لعبة. فيه:
  - تشغيل/إطفاء كل مؤشر independently
  - معايرة معاملات كل مؤشر (parameter sweep)
  - اختبار مؤشر واحد منفرد
  - جمع إشارات كل المؤشرات + ترجيح + احتمال
  - اختبار سلسلة أخبار على المؤشرات
  - نتائج حقيقية قابلة للتصدير
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backtest.indicators.indicators import (
    INDICATOR_REGISTRY, BaseIndicator, CalibrationResult,
    IndicatorSignal, SignalDir, create_indicator, list_indicators,
)
from backtest.models import Candle, Tick
from backtest.metrics import compute_metrics, build_equity_curve

log = logging.getLogger("backtest.lab")


# ═══════════════════════════════════════════════════════════════════════════════
# حالة المختبر
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class IndicatorSlot:
    """خانة مؤشر واحد بالمختبر — يشتغل أو مطفي."""
    name: str
    indicator: BaseIndicator
    enabled: bool = True
    weight: float = 1.0  # الترجيح (0-2)
    custom_params: dict[str, float] = field(default_factory=dict)
    last_signal: IndicatorSignal | None = None
    calibration: CalibrationResult | None = None


@dataclass
class LabRunResult:
    """نتيجة جلسة مختبر."""
    run_id: str = ""
    started_at: float = 0
    finished_at: float = 0
    status: str = "idle"  # idle, running, completed, failed
    # إعدادات
    symbol: str = ""
    candle_count: int = 0
    active_indicators: list[str] = field(default_factory=list)
    # نتائج مجمّعة
    consensus: str = SignalDir.NEUTRAL  # BUY/SELL/NEUTRAL
    consensus_strength: float = 0.0
    buy_signals: int = 0
    sell_signals: int = 0
    neutral_signals: int = 0
    probability_buy: float = 0.0
    probability_sell: float = 0.0
    # تفاصيل كل مؤشر
    indicator_signals: list[dict[str, Any]] = field(default_factory=list)
    # نتائج المعايرة
    calibration_results: list[dict[str, Any]] = field(default_factory=list)
    # أخبار
    news_impact: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "symbol": self.symbol,
            "candle_count": self.candle_count,
            "active_indicators": self.active_indicators,
            "consensus": self.consensus,
            "consensus_strength": round(self.consensus_strength, 4),
            "buy_signals": self.buy_signals,
            "sell_signals": self.sell_signals,
            "neutral_signals": self.neutral_signals,
            "probability_buy": round(self.probability_buy, 4),
            "probability_sell": round(self.probability_sell, 4),
            "indicator_signals": self.indicator_signals,
            "calibration_results": self.calibration_results,
            "news_impact": self.news_impact,
            "duration_s": round(self.finished_at - self.started_at, 3),
            "error": self.error,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# المختبر
# ═══════════════════════════════════════════════════════════════════════════════

class IndicatorLab:
    """المختبر الحقيقي — 15 مؤشر + معايرة + أخبار + احتمالات."""

    def __init__(self):
        self.slots: dict[str, IndicatorSlot] = {}
        self._init_slots()
        self._last_result: LabRunResult | None = None
        self._history: list[LabRunResult] = []

    def _init_slots(self):
        """تهيئة كل المؤشرات الـ15 — الكل مشغّل افتراضياً."""
        for name, cls in INDICATOR_REGISTRY.items():
            ind = cls()
            self.slots[name] = IndicatorSlot(
                name=name, indicator=ind, enabled=True, weight=1.0
            )

    # ═══ التحكم بالمؤشرات ═══

    def enable(self, name: str, enabled: bool = True) -> None:
        """تشغيل/إطفاء مؤشر."""
        if name in self.slots:
            self.slots[name].enabled = enabled

    def disable(self, name: str) -> None:
        """إطفاء مؤشر."""
        self.enable(name, False)

    def set_weight(self, name: str, weight: float) -> None:
        """تعيين ترجيح لمؤشر (0-2)."""
        if name in self.slots:
            self.slots[name].weight = max(0, min(2, weight))

    def set_params(self, name: str, params: dict[str, float]) -> None:
        """تعيين معاملات مخصصة لمؤشر."""
        if name in self.slots:
            slot = self.slots[name]
            slot.custom_params = params
            # إعادة إنشاء المؤشر بالمعاملات الجديدة
            slot.indicator = create_indicator(name, params)
            slot.indicator.enabled = slot.enabled

    def toggle(self, name: str) -> bool:
        """تبديل حالة مؤشر — يرجع الحالة الجديدة."""
        if name in self.slots:
            self.slots[name].enabled = not self.slots[name].enabled
            return self.slots[name].enabled
        return False

    def get_status(self) -> dict[str, Any]:
        """حالة كل مؤشر بالمختبر."""
        return {
            name: {
                "enabled": slot.enabled,
                "weight": slot.weight,
                "last_signal": {
                    "direction": slot.last_signal.direction,
                    "strength": round(slot.last_signal.strength, 3),
                    "value": round(slot.last_signal.value, 5),
                } if slot.last_signal else None,
                "calibrated": slot.calibration is not None,
            }
            for name, slot in self.slots.items()
        }

    def reset_all(self) -> None:
        """إعادة تهيئة كل المؤشرات."""
        for slot in self.slots.values():
            slot.indicator.reset()
            slot.last_signal = None

    # ═══ التشغيل ═══

    def run(self, candles: list[Candle], symbol: str = "") -> LabRunResult:
        """تشغيل المختبر على سلسلة شموع — يرجع نتيجة مجمّعة."""
        result = LabRunResult(
            run_id=str(uuid.uuid4())[:8],
            started_at=time.time(),
            symbol=symbol,
            status="running",
        )

        try:
            active = [s for s in self.slots.values() if s.enabled]
            result.active_indicators = [s.name for s in active]

            if not active:
                result.status = "failed"
                result.error = "لا يوجد مؤشر مشغّل"
                return result

            # إعادة تهيئة
            for slot in active:
                slot.indicator.reset()

            # تمرير الشموع
            buy_total = 0.0
            sell_total = 0.0
            weight_total = 0.0
            signal_details = []

            for candle in candles:
                for slot in active:
                    slot.indicator.update(candle)

            # جمع الإشارات النهائية
            for slot in active:
                sig = slot.indicator.signal()
                slot.last_signal = sig
                w = slot.weight

                info = {
                    "name": slot.name,
                    "display_name": slot.indicator.display_name,
                    "direction": sig.direction,
                    "strength": round(sig.strength, 3),
                    "value": round(sig.value, 5),
                    "weight": w,
                    "category": slot.indicator.category,
                    "details": sig.details,
                }
                signal_details.append(info)

                if sig.direction == SignalDir.BUY:
                    buy_total += sig.strength * w
                elif sig.direction == SignalDir.SELL:
                    sell_total += sig.strength * w
                weight_total += w

            result.indicator_signals = signal_details
            result.candle_count = len(candles)
            result.buy_signals = sum(1 for s in signal_details if s["direction"] == SignalDir.BUY)
            result.sell_signals = sum(1 for s in signal_details if s["direction"] == SignalDir.SELL)
            result.neutral_signals = sum(1 for s in signal_details if s["direction"] == SignalDir.NEUTRAL)

            # حساب الإجماع
            total_strength = buy_total + sell_total
            if total_strength > 0:
                result.probability_buy = buy_total / (buy_total + sell_total) if (buy_total + sell_total) > 0 else 0.5
                result.probability_sell = 1 - result.probability_buy
            else:
                result.probability_buy = 0.5
                result.probability_sell = 0.5

            if buy_total > sell_total and buy_total > 0:
                result.consensus = SignalDir.BUY
                result.consensus_strength = min(buy_total / max(weight_total, 1), 1.0)
            elif sell_total > buy_total and sell_total > 0:
                result.consensus = SignalDir.SELL
                result.consensus_strength = min(sell_total / max(weight_total, 1), 1.0)
            else:
                result.consensus = SignalDir.NEUTRAL
                result.consensus_strength = 0.0

            result.status = "completed"
            result.finished_at = time.time()

        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
            log.error(f"فشل المختبر: {exc}", exc_info=True)

        self._last_result = result
        self._history.append(result)
        return result

    # ═══ اختبار مؤشر منفرد ═══

    def test_single(self, indicator_name: str, candles: list[Candle],
                    params: dict[str, float] | None = None) -> dict[str, Any]:
        """اختبار مؤشر واحد على بيانات — يرجع تفاصيل كاملة."""
        ind = create_indicator(indicator_name, params)
        signals_history: list[dict] = []
        values_history: list[float] = []

        for candle in candles:
            val = ind.update(candle)
            values_history.append(val)

        # آخر إشارة
        sig = ind.signal()
        # إشارات عبر التاريخ (كل 10 شموع)
        ind.reset()
        for i, candle in enumerate(candles):
            ind.update(candle)
            if i % max(len(candles) // 50, 1) == 0:
                s = ind.signal()
                signals_history.append({
                    "index": i,
                    "timestamp": candle.timestamp,
                    "direction": s.direction,
                    "strength": round(s.strength, 3),
                    "value": round(s.value, 5),
                })

        return {
            "name": indicator_name,
            "display_name": ind.display_name,
            "candle_count": len(candles),
            "final_signal": {
                "direction": sig.direction,
                "strength": round(sig.strength, 3),
                "value": round(sig.value, 5),
                "details": sig.details,
            },
            "signals_over_time": signals_history,
            "values": values_history[-200:],  # آخر 200 قيمة
            "params": ind.params,
        }

    # ═══ المعايرة ═══

    def calibrate(self, indicator_name: str, candles: list[Candle],
                  future_candles: list[Candle] | None = None) -> CalibrationResult:
        """معايرة مؤشر — parameter sweep لإيجاد أفضل معاملات.

        الطريقة:
        1. نجرب كل مجموعة معاملات ممكنة
        2. نحسب نسبة الفوز لكل مجموعة
        3. نرجع أفضل مجموعة
        """
        ind_cls = INDICATOR_REGISTRY.get(indicator_name)
        if not ind_cls:
            raise ValueError(f"مؤشر غير معروف: {indicator_name}")

        default = ind_cls.default_params
        param_sweep = self._generate_param_sweep(indicator_name, default)
        best_params = dict(default)
        best_win_rate = 0.0
        best_return = 0.0
        best_sharpe = 0.0
        sweep_results = []

        for params in param_sweep:
            ind = ind_cls(params)
            trades = self._simulate_indicator_trades(ind, candles, future_candles)
            if not trades:
                continue

            # حساب مقاييس
            from backtest.models import EquityPoint
            pnls = [t.net_pnl for t in trades if t.exit_price is not None]
            wins = [p for p in pnls if p > 0]
            win_rate = len(wins) / len(pnls) if pnls else 0
            avg_ret = sum(pnls) / len(pnls) if pnls else 0
            sharpe = 0.0
            if len(pnls) >= 2:
                mean_p = sum(pnls) / len(pnls)
                var_p = sum((p - mean_p) ** 2 for p in pnls) / (len(pnls) - 1)
                std_p = math.sqrt(var_p) if var_p > 0 else 0
                sharpe = mean_p / std_p if std_p > 0 else 0

            sweep_results.append({
                "params": params,
                "win_rate": round(win_rate, 4),
                "avg_return": round(avg_ret, 6),
                "sharpe": round(sharpe, 4),
                "trades": len(pnls),
            })

            if win_rate > best_win_rate or (win_rate == best_win_rate and avg_ret > best_return):
                best_win_rate = win_rate
                best_return = avg_ret
                best_sharpe = sharpe
                best_params = dict(params)

        # ترتيب حسب win_rate
        sweep_results.sort(key=lambda x: (-x["win_rate"], -x["sharpe"]))

        result = CalibrationResult(
            name=indicator_name,
            optimal_params=best_params,
            win_rate=best_win_rate,
            avg_return=best_return,
            sharpe=best_sharpe,
            max_drawdown=0,  # نحسبه من equity curve
            trades_tested=len(sweep_results),
            param_sweep=sweep_results[:20],  # أفضل 20
        )

        # حفظ بالمختبر
        if indicator_name in self.slots:
            self.slots[indicator_name].calibration = result

        return result

    def _generate_param_sweep(self, name: str, default: dict) -> list[dict]:
        """توليد مجموعات معاملات للمعايرة."""
        sweeps: list[dict] = [dict(default)]  # الافتراضي دائماً

        for key, val in default.items():
            if "period" in key.lower():
                for delta in [-5, -3, -2, 2, 3, 5, 8, 10]:
                    new_val = max(2, int(val) + delta)
                    p = dict(default)
                    p[key] = float(new_val)
                    sweeps.append(p)
            elif "dev" in key.lower() or "deviation" in key.lower():
                for delta in [-0.5, -0.25, 0.25, 0.5, 1.0]:
                    new_val = max(0.5, val + delta)
                    p = dict(default)
                    p[key] = new_val
                    sweeps.append(p)
            elif "overbought" in key.lower():
                for new_val in [65, 75, 80, 85]:
                    p = dict(default)
                    p[key] = float(new_val)
                    sweeps.append(p)
            elif "oversold" in key.lower():
                for new_val in [15, 20, 25, 35]:
                    p = dict(default)
                    p[key] = float(new_val)
                    sweeps.append(p)
            elif "fast" in key.lower():
                for new_val in [5, 8, 12, 15]:
                    p = dict(default)
                    p[key] = float(new_val)
                    sweeps.append(p)
            elif "slow" in key.lower():
                for new_val in [20, 26, 30, 40, 50]:
                    p = dict(default)
                    p[key] = float(new_val)
                    sweeps.append(p)

        return sweeps

    def _simulate_indicator_trades(self, indicator: BaseIndicator,
                                   candles: list[Candle],
                                   future: list[Candle] | None = None) -> list:
        """محاكاة صفقات بناءً على إشارات مؤشر — للمعايرة."""
        from backtest.models import Trade, Side

        trades: list[Trade] = []
        open_trade: Trade | None = None
        trade_id = 0

        check_candles = future or candles[len(candles) * 2 // 3:]  # نختبر على آخر ثلث

        for candle in candles:
            indicator.update(candle)

        # نرجّع المؤشر ونحاكي على آخر جزء
        indicator.reset()
        entry_idx = len(candles) - len(check_candles)

        for i, candle in enumerate(candles):
            indicator.update(candle)
            if i < entry_idx:
                continue

            sig = indicator.signal()

            if open_trade is None:
                if sig.direction == SignalDir.BUY and sig.strength > 0.3:
                    trade_id += 1
                    open_trade = Trade(
                        id=trade_id, symbol="", side=Side.BUY,
                        entry_price=candle.close, entry_time=candle.timestamp,
                    )
                elif sig.direction == SignalDir.SELL and sig.strength > 0.3:
                    trade_id += 1
                    open_trade = Trade(
                        id=trade_id, symbol="", side=Side.SELL,
                        entry_price=candle.close, entry_time=candle.timestamp,
                    )
            else:
                should_close = False
                if open_trade.side == Side.BUY and sig.direction == SignalDir.SELL:
                    should_close = True
                elif open_trade.side == Side.SELL and sig.direction == SignalDir.BUY:
                    should_close = True

                if should_close:
                    open_trade.close(candle.close, candle.timestamp, reason="signal")
                    trades.append(open_trade)
                    open_trade = None

        # إغلاق أي صفقة مفتوحة
        if open_trade and candles:
            open_trade.close(candles[-1].close, candles[-1].timestamp, reason="end")
            trades.append(open_trade)

        return trades

    # ═══ اختبار الأخبار ═══

    def test_news_impact(self, news_events: list[dict[str, Any]],
                         candles: list[Candle]) -> list[dict[str, Any]]:
        """اختبار تأثير سلسلة أحداث أخبارية على المؤشرات.

        كل حدث خبري:
          {"timestamp": float, "impact": "high"|"medium"|"low",
           "direction": "positive"|"negative", "headline": str}

        نحلل: كيف تغيّرت إشارات المؤشرات قبل/بعد كل خبر.
        """
        results = []
        window = 20  # شموع قبل وبعد

        for event in news_events:
            ts = event.get("timestamp", 0)
            # إيجاد موقع الخبر بالشموع
            idx = 0
            for i, c in enumerate(candles):
                if c.timestamp >= ts:
                    idx = i
                    break

            before_candles = candles[max(0, idx - window):idx]
            after_candles = candles[idx:min(len(candles), idx + window)]

            # نحلل كل مؤشر
            indicator_changes = []
            for slot in self.slots.values():
                if not slot.enabled:
                    continue
                slot.indicator.reset()
                for c in before_candles:
                    slot.indicator.update(c)
                sig_before = slot.indicator.signal()

                for c in after_candles:
                    slot.indicator.update(c)
                sig_after = slot.indicator.signal()

                indicator_changes.append({
                    "indicator": slot.name,
                    "before": {"direction": sig_before.direction, "strength": round(sig_before.strength, 3)},
                    "after": {"direction": sig_after.direction, "strength": round(sig_after.strength, 3)},
                    "changed": sig_before.direction != sig_after.direction,
                })

            # حركة السعر
            price_before = before_candles[-1].close if before_candles else 0
            price_after = after_candles[-1].close if after_candles else 0
            price_change = ((price_after - price_before) / price_before * 100) if price_before > 0 else 0

            results.append({
                "headline": event.get("headline", ""),
                "impact": event.get("impact", "medium"),
                "timestamp": ts,
                "price_change_pct": round(price_change, 4),
                "indicators_changed": sum(1 for c in indicator_changes if c["changed"]),
                "total_indicators": len(indicator_changes),
                "details": indicator_changes,
            })

        return results

    # ═══ التصدير ═══

    def export_result(self, result: LabRunResult | None = None) -> str:
        """تصدير نتيجة كـ JSON."""
        r = result or self._last_result
        if r is None:
            return json.dumps({"error": "لا توجد نتيجة"})
        return json.dumps(r.to_dict(), ensure_ascii=False, indent=2)

    def save_result(self, path: str | Path, result: LabRunResult | None = None) -> None:
        """حفظ نتيجة لملف."""
        r = result or self._last_result
        if r is None:
            return
        Path(path).write_text(self.export_result(r), encoding="utf-8")
