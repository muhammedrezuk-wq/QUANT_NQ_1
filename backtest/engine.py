# -*- coding: utf-8 -*-
"""المحرك الرئيسي للاختبارات الرجعية — Backtest Engine.

يشغّل استراتيجية على بيانات تاريخية (تيكات) ويحسب النتائج:
  1. يمرّ على كل تيك بالترتيب
  2. يُمرّره للاستراتيجية
  3. ينفّذ إشارات الاستراتيجية (فتح/إغلاق صفقات)
  4. يُحدّث حقوق الملكية ومنحنى التراجع
  5. يحسب المقاييس النهائية

يدعم أيضاً التشغيل على شموع بدل التيكات (أسرع).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from backtest.data_feed import DataFeed, generate_synthetic_data, load_from_file, load_from_websocket
from backtest.metrics import build_equity_curve, compute_metrics
from backtest.models import (
    BacktestConfig, BacktestResult, Candle, EquityPoint, Side,
    Tick, Trade, TradeStatus,
)
from backtest.strategies import BaseStrategy, create_strategy

log = logging.getLogger("backtest.engine")


class BacktestEngine:
    """المحرك — يُشغّل جولة باك تست واحدة."""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.result = BacktestResult(
            run_id=str(uuid.uuid4())[:8],
            config=config,
        )
        self._strategy: BaseStrategy | None = None
        self._equity = config.initial_capital
        self._peak_equity = config.initial_capital
        self._open_trade: Trade | None = None
        self._trade_counter = 0
        self._equity_points: list[EquityPoint] = []
        self._running = False

    async def run(self, feed: DataFeed | None = None) -> BacktestResult:
        """تشغيل البكتست على بيانات موجودة أو جلبها تلقائياً."""
        self.result.started_at = time.time()
        self.result.status = "running"

        try:
            # إنشاء الاستراتيجية
            self._strategy = create_strategy(
                self.config.strategy_name,
                self.config.strategy_params,
            )

            # جلب البيانات إن لم تُقدَّم
            if feed is None:
                feed = await self._load_data()

            # التشغيل
            if feed.ticks:
                await self._run_on_ticks(feed)
            elif feed.candles:
                await self._run_on_candles(feed)
            else:
                self.result.status = "failed"
                self.result.error = "لا توجد بيانات — لا تيكات ولا شموع"
                return self.result

            # إغلاق أي صفقة مفتوحة
            self._close_all_at_end(feed)

            # بناء منحنى حقوق الملكية
            self.result.equity_curve = build_equity_curve(
                self.result.trades, self.config.initial_capital
            )

            # حساب المقاييس
            metrics = compute_metrics(
                self.result.trades,
                self.result.equity_curve,
                self.config.initial_capital,
            )
            for key, value in metrics.items():
                setattr(self.result, key, value)

            self.result.finished_at = time.time()
            self.result.duration_s = self.result.finished_at - self.result.started_at
            self.result.ticks_count = feed.total_ticks
            self.result.status = "completed"
            self.result.candles = feed.candles[-500:]  # آخر 500 شمعة للعرض

            log.info(
                f"بكتست {self.result.run_id} اكتمل: "
                f"{self.result.total_trades} صفقة، "
                f"PnL={self.result.net_pnl:+.2f}، "
                f"WinRate={self.result.win_rate:.1%}"
            )

        except Exception as exc:
            self.result.status = "failed"
            self.result.error = str(exc)
            log.error(f"فشل البكتست: {exc}", exc_info=True)

        return self.result

    async def _load_data(self) -> DataFeed:
        """تحميل البيانات حسب الإعدادات.

        حرج ٣ — لا سقوط اصطناعي صامت:
        - إن وُجد ملف بيانات → حمّله
        - إن وُضبط WebSocket → حاوله، وإن فشل ارمِ استثناءً (لا اصطناعي تلقائي)
        - إن طُلب صراحةً allow_synthetic=True → اصطناعي مُعلَن
        """
        # 1. ملف بيانات — الأولوية الأولى
        if self.config.data_file:
            try:
                feed = await load_from_file(self.config.data_file)
                self.result.data_source = f"file:{self.config.data_file}"
                return feed
            except Exception as exc:
                raise RuntimeError(f"فشل تحميل ملف البيانات: {self.config.data_file}: {exc}") from exc

        # 2. WebSocket — إن وُضبّط
        if self.config.ws_port:
            try:
                feed = await load_from_websocket(
                    self.config.ws_host, self.config.ws_port,
                    self.config.symbol, timeout_s=10.0,
                )
                self.result.data_source = f"ws:{self.config.ws_host}:{self.config.ws_port}"
                return feed
            except Exception as exc:
                # حرج ٣: لا سقوط اصطناعي — ارمِ الخطأ
                raise RuntimeError(
                    f"WebSocket غير متاح ({exc}). "
                    f"قدّم ملف بيانات (data_file) أو فعّل allow_synthetic=True."
                ) from exc

        # 3. اصطناعي صريح فقط — إن طُلب
        if getattr(self.config, 'allow_synthetic', False):
            feed = await generate_synthetic_data(
                symbol=self.config.symbol,
                num_ticks=10000,
                start_price=1.0850 if "EUR" in self.config.symbol else 2000.0,
            )
            self.result.data_source = "synthetic:explicit"
            log.warning("⚠️ تشغيل ببيانات اصطناعية صريحة — النتائج غير موثوقة")
            return feed

        # 4. لا شيء متاح — فشل صريح
        raise RuntimeError(
            "لا توجد بيانات. قدّم: data_file (ملف) أو ws_port (WebSocket) "
            "أو allow_synthetic=True (اصطناعي صريح)."
        )

    async def _run_on_ticks(self, feed: DataFeed) -> None:
        """تشغيل على تيكات فردية (دقة عالية)."""
        self._running = True
        equity_sample_count = 0

        for tick in feed.ticks:
            if not self._running:
                break

            # فلتر الفترة الزمنية
            if self.config.start_time and tick.timestamp < self.config.start_time:
                continue
            if self.config.end_time and tick.timestamp > self.config.end_time:
                break

            # فحص SL/TP للصفقة المفتوحة
            self._check_stop_levels(tick)

            # تمرير التيك للاستراتيجية
            if self._strategy:
                self._strategy.on_tick(tick)
                signals = self._strategy.drain_signals()
                for signal in signals:
                    self._execute_signal(signal, tick)

            # عيّنة حقوق الملكية كل 500 تيك
            equity_sample_count += 1
            if equity_sample_count >= 500:
                self._record_equity(tick.timestamp)
                equity_sample_count = 0

        # عيّنة أخيرة
        if feed.ticks:
            self._record_equity(feed.ticks[-1].timestamp)

    async def _run_on_candles(self, feed: DataFeed) -> None:
        """تشغيل على شموع (أسرع — أقل دقة)."""
        self._running = True

        for candle in feed.candles:
            if not self._running:
                break

            # فحص SL/TP
            self._check_stop_levels_candle(candle)

            # تمرير الشمعة للاستراتيجية
            if self._strategy:
                # إنشاء تيك وهمي من الشمعة للاستراتيجية
                tick = Tick(
                    symbol=candle.symbol,
                    timestamp=candle.timestamp,
                    bid=candle.close,
                    ask=candle.close + 0.0001,
                    volume=candle.volume,
                )
                self._strategy.on_tick(tick)
                self._strategy.on_candle(candle)
                signals = self._strategy.drain_signals()
                for signal in signals:
                    self._execute_signal(signal, tick)

            # تسجيل حقوق الملكية
            self._record_equity(candle.timestamp)

    def _execute_signal(self, signal: Any, tick: Tick) -> None:
        """تنفيذ إشارة من الاستراتيجية."""
        if signal.action == "OPEN":
            # حرج ٤: عدّ الصفقات المفتوحة فعلياً — لا التراكمي
            # _trade_counter عدّاد إجمالي (لا يُنقص عند الإغلاق) — لا يصلح لمقارنة بـ max_open
            open_count = 1 if self._open_trade is not None else 0
            if open_count >= self.config.max_open_trades:
                return

            self._trade_counter += 1
            price = tick.ask if signal.side == Side.BUY else tick.bid
            # إضافة سلبج
            if self.config.slippage_pips > 0:
                slippage = self.config.slippage_pips * 0.0001
                if signal.side == Side.BUY:
                    price += slippage
                else:
                    price -= slippage

            self._open_trade = Trade(
                id=self._trade_counter,
                symbol=tick.symbol or self.config.symbol,
                side=signal.side,
                entry_price=price,
                entry_time=tick.timestamp,
                size=self.config.lot_size,
                commission=self.config.commission_per_lot * self.config.lot_size,
            )

        elif signal.action == "CLOSE":
            if self._open_trade is None:
                return
            price = tick.bid if self._open_trade.side == Side.BUY else tick.ask
            self._open_trade.close(price, tick.timestamp, reason=signal.reason or "")
            self._equity += self._open_trade.net_pnl
            self.result.trades.append(self._open_trade)
            self._open_trade = None

    def _check_stop_levels(self, tick: Tick) -> None:
        """فحص مستويات وقف الخسارة وجني الربح على تيك."""
        if self._open_trade is None:
            return

        trade = self._open_trade
        if trade.stop_loss is not None:
            if trade.side == Side.BUY and tick.bid <= trade.stop_loss:
                trade.close(trade.stop_loss, tick.timestamp, reason="Stop Loss")
                self._equity += trade.net_pnl
                self.result.trades.append(trade)
                self._open_trade = None
                return
            if trade.side == Side.SELL and tick.ask >= trade.stop_loss:
                trade.close(trade.stop_loss, tick.timestamp, reason="Stop Loss")
                self._equity += trade.net_pnl
                self.result.trades.append(trade)
                self._open_trade = None
                return

        if trade.take_profit is not None:
            if trade.side == Side.BUY and tick.bid >= trade.take_profit:
                trade.close(trade.take_profit, tick.timestamp, reason="Take Profit")
                self._equity += trade.net_pnl
                self.result.trades.append(trade)
                self._open_trade = None
                return
            if trade.side == Side.SELL and tick.ask <= trade.take_profit:
                trade.close(trade.take_profit, tick.timestamp, reason="Take Profit")
                self._equity += trade.net_pnl
                self.result.trades.append(trade)
                self._open_trade = None
                return

    def _check_stop_levels_candle(self, candle: Candle) -> None:
        """فحص SL/TP على شمعة (أقل دقة)."""
        if self._open_trade is None:
            return

        trade = self._open_trade
        if trade.stop_loss is not None:
            if trade.side == Side.BUY and candle.low <= trade.stop_loss:
                trade.close(trade.stop_loss, candle.timestamp, reason="Stop Loss")
                self._equity += trade.net_pnl
                self.result.trades.append(trade)
                self._open_trade = None
                return
            if trade.side == Side.SELL and candle.high >= trade.stop_loss:
                trade.close(trade.stop_loss, candle.timestamp, reason="Stop Loss")
                self._equity += trade.net_pnl
                self.result.trades.append(trade)
                self._open_trade = None
                return

        if trade.take_profit is not None:
            if trade.side == Side.BUY and candle.high >= trade.take_profit:
                trade.close(trade.take_profit, candle.timestamp, reason="Take Profit")
                self._equity += trade.net_pnl
                self.result.trades.append(trade)
                self._open_trade = None
                return
            if trade.side == Side.SELL and candle.low <= trade.take_profit:
                trade.close(trade.take_profit, candle.timestamp, reason="Take Profit")
                self._equity += trade.net_pnl
                self.result.trades.append(trade)
                self._open_trade = None
                return

    def _record_equity(self, timestamp: float) -> None:
        """تسجيل نقطة على منحنى حقوق الملكية."""
        unrealized = 0.0
        if self._open_trade and self._open_trade.is_open:
            # حساب الربح غير المحقّق
            # نحتاج آخر سعر — نستخدم equity كنسبة تقريبية
            pass
        current_equity = self._equity + unrealized
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        dd = self._peak_equity - current_equity
        dd_pct = dd / self._peak_equity if self._peak_equity > 0 else 0.0
        self._equity_points.append(EquityPoint(
            timestamp=timestamp,
            equity=current_equity,
            drawdown=dd,
            drawdown_pct=dd_pct,
        ))

    def _close_all_at_end(self, feed: DataFeed) -> None:
        """إغلاق كل الصفقات المفتوحة في نهاية البكتست."""
        if self._open_trade and self._open_trade.is_open:
            last_price = self._open_trade.entry_price  # افتراضي
            last_time = self._open_trade.entry_time
            if feed.ticks:
                last_price = feed.ticks[-1].mid
                last_time = feed.ticks[-1].timestamp
            elif feed.candles:
                last_price = feed.candles[-1].close
                last_time = feed.candles[-1].timestamp
            self._open_trade.close(last_price, last_time, reason="End of backtest")
            self._equity += self._open_trade.net_pnl
            self.result.trades.append(self._open_trade)
            self._open_trade = None

    def stop(self) -> None:
        """إيقاف البكتست (للبكتست الطويل)."""
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════════
# واجهة مبسّطة — تشغيل سريع
# ═══════════════════════════════════════════════════════════════════════════════

async def run_backtest(
    strategy_name: str = "ma_crossover",
    strategy_params: dict[str, Any] | None = None,
    symbol: str = "EURUSD",
    initial_capital: float = 100_000.0,
    lot_size: float = 0.01,
    data_file: str | None = None,
    ws_host: str = "127.0.0.1",
    ws_port: int = 8765,
) -> BacktestResult:
    """تشغيل باك تست سريع — واجهة مبسّطة.

    مثال:
        result = await run_backtest("ma_crossover", {"fast_period": 5, "slow_period": 20})
    """
    config = BacktestConfig(
        symbol=symbol,
        initial_capital=initial_capital,
        lot_size=lot_size,
        strategy_name=strategy_name,
        strategy_params=strategy_params or {},
        ws_host=ws_host,
        ws_port=ws_port,
    )
    engine = BacktestEngine(config)

    feed = None
    if data_file:
        feed = await load_from_file(data_file)

    return await engine.run(feed)
