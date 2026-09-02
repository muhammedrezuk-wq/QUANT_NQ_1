# -*- coding: utf-8 -*-
"""اختبارات محرك البكتست — backtest engine test suite."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from backtest.engine import BacktestEngine, run_backtest
from backtest.metrics import build_equity_curve, compute_metrics
from backtest.models import (
    BacktestConfig, Candle, EquityPoint, Side, Tick, Trade, TradeStatus,
)
from backtest.strategies import (
    BaseStrategy, BreakoutStrategy, MeanReversionStrategy,
    MovingAverageCrossover, Signal, create_strategy, list_strategies,
)


# ═══════════════════════════════════════════════════════════════════════════════
# اختبار النماذج
# ═══════════════════════════════════════════════════════════════════════════════

class TestModels:
    def test_tick_mid_and_spread(self):
        t = Tick(symbol="EURUSD", timestamp=1000.0, bid=1.08500, ask=1.08510)
        assert t.mid == pytest.approx(1.08505)
        assert t.spread == pytest.approx(0.00010)

    def test_tick_spread_pips_eurusd(self):
        t = Tick(symbol="EURUSD", timestamp=0, bid=1.0, ask=1.00010)
        assert t.spread_pips == pytest.approx(1.0)

    def test_tick_spread_pips_jpy(self):
        t = Tick(symbol="USDJPY", timestamp=0, bid=150.00, ask=150.01)
        assert t.spread_pips == pytest.approx(1.0)

    def test_candle_properties(self):
        c = Candle(symbol="X", timestamp=0, open=1.0, high=1.5, low=0.8, close=1.3)
        assert c.body == pytest.approx(0.3)
        assert c.range == pytest.approx(0.7)
        assert c.is_bullish is True

    def test_candle_bearish(self):
        c = Candle(symbol="X", timestamp=0, open=1.5, high=1.6, low=1.0, close=1.2)
        assert c.is_bullish is False

    def test_trade_open_close(self):
        t = Trade(id=1, symbol="EURUSD", side=Side.BUY, entry_price=1.0850,
                  entry_time=1000.0, size=1.0)
        assert t.is_open
        t.close(1.0900, 2000.0, reason="Target")
        assert not t.is_open
        assert t.pnl == pytest.approx(0.0050)
        assert t.duration_s == 1000.0

    def test_trade_sell_pnl(self):
        t = Trade(id=2, symbol="EURUSD", side=Side.SELL, entry_price=1.0900,
                  entry_time=1000.0, size=1.0)
        t.close(1.0850, 2000.0)
        assert t.pnl == pytest.approx(0.0050)

    def test_trade_net_pnl(self):
        t = Trade(id=3, symbol="X", side=Side.BUY, entry_price=1.0, entry_time=0,
                  size=1.0, commission=0.5)
        t.close(1.1, 100.0)
        assert t.net_pnl == pytest.approx(0.1 - 0.5)

    def test_backtest_result_to_dict(self):
        from backtest.models import BacktestResult
        r = BacktestResult(run_id="test123")
        r.status = "completed"
        d = r.to_dict()
        assert d["run_id"] == "test123"
        assert d["status"] == "completed"
        assert "metrics" in d
        assert "trades" in d
        assert "equity_curve" in d


# ═══════════════════════════════════════════════════════════════════════════════
# اختبار الاستراتيجيات
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategies:
    def _make_ticks(self, prices: list[float], symbol="EURUSD") -> list[Tick]:
        return [Tick(symbol=symbol, timestamp=float(i), bid=p - 0.00005, ask=p + 0.00005)
                for i, p in enumerate(prices)]

    def test_create_strategy(self):
        s = create_strategy("ma_crossover")
        assert isinstance(s, MovingAverageCrossover)

    def test_create_unknown_strategy(self):
        with pytest.raises(ValueError, match="غير معروفة"):
            create_strategy("nonexistent")

    def test_list_strategies(self):
        st = list_strategies()
        assert len(st) >= 3
        names = [s["name"] for s in st]
        assert "ma_crossover" in names
        assert "breakout" in names
        assert "mean_reversion" in names

    def test_ma_crossover_generates_signals(self):
        """صعود → تقاطع صاعد → إشارة شراء."""
        s = MovingAverageCrossover({"fast_period": 3, "slow_period": 5})
        # بيانات صاعدة
        prices = [1.0] * 5 + [1.001, 1.002, 1.003, 1.004, 1.005, 1.006, 1.007]
        for tick in self._make_ticks(prices):
            s.on_tick(tick)
        signals = s.drain_signals()
        assert any(sig.action == "OPEN" and sig.side == Side.BUY for sig in signals)

    def test_ma_crossover_reset(self):
        s = MovingAverageCrossover()
        s.signals.append(Signal("OPEN", side=Side.BUY))
        s.reset()
        assert len(s.signals) == 0

    def test_breakout_strategy(self):
        s = BreakoutStrategy({"lookback": 5, "exit_lookback": 3})
        # تيكات مستقرة ثم اختراق
        prices = [1.0] * 5 + [1.005]
        for tick in self._make_ticks(prices):
            s.on_tick(tick)
        signals = s.drain_signals()
        assert any(sig.action == "OPEN" for sig in signals)

    def test_mean_reversion_strategy(self):
        s = MeanReversionStrategy({"period": 5, "std_dev": 1.0})
        # أسعار مستقرة ثم هبوط حاد
        prices = [1.0] * 5 + [0.98]
        for tick in self._make_ticks(prices):
            s.on_tick(tick)
        signals = s.drain_signals()
        assert any(sig.action == "OPEN" and sig.side == Side.BUY for sig in signals)


# ═══════════════════════════════════════════════════════════════════════════════
# اختبار المقاييس
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def _make_trades(self, pnls: list[float]) -> list[Trade]:
        trades = []
        t = 1000.0
        for i, pnl in enumerate(pnls):
            trade = Trade(id=i + 1, symbol="X", side=Side.BUY,
                          entry_price=1.0, entry_time=t, size=1.0)
            trade.close(1.0 + pnl, t + 100, reason="test")
            trades.append(trade)
            t += 200
        return trades

    def test_empty_metrics(self):
        m = compute_metrics([], [], 100_000)
        assert m["total_trades"] == 0
        assert m["final_equity"] == 100_000

    def test_all_wins(self):
        trades = self._make_trades([100, 200, 150])
        m = compute_metrics(trades, [], 100_000)
        assert m["total_trades"] == 3
        assert m["winning_trades"] == 3
        assert m["losing_trades"] == 0
        assert m["win_rate"] == 1.0
        assert m["net_pnl"] == pytest.approx(450)

    def test_mixed_trades(self):
        trades = self._make_trades([100, -50, 200, -30, 150])
        m = compute_metrics(trades, [], 100_000)
        assert m["total_trades"] == 5
        assert m["winning_trades"] == 3
        assert m["losing_trades"] == 2
        assert m["win_rate"] == pytest.approx(0.6)
        assert m["net_pnl"] == pytest.approx(370)

    def test_profit_factor(self):
        trades = self._make_trades([100, -50, 200])
        m = compute_metrics(trades, [], 100_000)
        assert m["profit_factor"] == pytest.approx(300 / 50)

    def test_consecutive_wins(self):
        trades = self._make_trades([10, 20, 30, -5, 40, 50, 60])
        m = compute_metrics(trades, [], 100_000)
        assert m["max_consecutive_wins"] == 3

    def test_consecutive_losses(self):
        trades = self._make_trades([10, -5, -10, -15, 20])
        m = compute_metrics(trades, [], 100_000)
        assert m["max_consecutive_losses"] == 3

    def test_equity_curve_building(self):
        trades = self._make_trades([100, -50, 200, 100])
        curve = build_equity_curve(trades, 100_000)
        assert len(curve) >= 2
        assert curve[0].equity == 100_000  # نقطة البداية
        assert curve[-1].equity == pytest.approx(100_350)

    def test_drawdown_in_equity(self):
        trades = self._make_trades([100, 200, -500, 100])
        curve = build_equity_curve(trades, 100_000, equity_sample_interval=1)
        m = compute_metrics(trades, curve, 100_000)
        assert m["max_drawdown"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# اختبار المحرك
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngine:
    def _make_feed(self, num_ticks=5000, symbol="EURUSD"):
        from backtest.data_feed import DataFeed
        import random
        feed = DataFeed()
        price = 1.0850
        t = time.time() - num_ticks * 0.1
        for i in range(num_ticks):
            change = random.gauss(0, 0.0002)
            price = max(price + change, 0.001)
            feed._ticks.append(Tick(
                symbol=symbol, timestamp=t + i * 0.1,
                bid=price - 0.00005, ask=price + 0.00005,
                volume=random.randint(100, 5000),
            ))
        return feed

    @pytest.mark.asyncio
    async def test_engine_with_synthetic_data(self):
        config = BacktestConfig(
            symbol="EURUSD", initial_capital=100_000,
            lot_size=0.01, strategy_name="ma_crossover",
            strategy_params={"fast_period": 5, "slow_period": 15},
            ws_port=0,  # لا WebSocket
        )
        engine = BacktestEngine(config)
        feed = self._make_feed(3000)
        result = await engine.run(feed)
        assert result.status == "completed"
        assert result.ticks_count == 3000
        assert isinstance(result.total_trades, int)
        assert result.final_equity > 0

    @pytest.mark.asyncio
    async def test_engine_breakout_strategy(self):
        config = BacktestConfig(
            symbol="EURUSD", initial_capital=50_000,
            strategy_name="breakout",
            strategy_params={"lookback": 10},
            ws_port=0,
        )
        engine = BacktestEngine(config)
        feed = self._make_feed(2000)
        result = await engine.run(feed)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_engine_mean_reversion(self):
        config = BacktestConfig(
            symbol="EURUSD", initial_capital=100_000,
            strategy_name="mean_reversion",
            strategy_params={"period": 10, "std_dev": 1.5},
            ws_port=0,
        )
        engine = BacktestEngine(config)
        feed = self._make_feed(2000)
        result = await engine.run(feed)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_engine_empty_data(self):
        from backtest.data_feed import DataFeed
        config = BacktestConfig(strategy_name="ma_crossover", ws_port=0)
        engine = BacktestEngine(config)
        result = await engine.run(DataFeed())
        assert result.status == "failed"
        assert "لا توجد بيانات" in result.error

    @pytest.mark.asyncio
    async def test_run_backtest_convenience(self):
        result = await run_backtest(
            strategy_name="ma_crossover",
            strategy_params={"fast_period": 5, "slow_period": 10},
            symbol="EURUSD",
            initial_capital=100_000,
            ws_port=0,
        )
        assert result.status == "completed"
        assert result.run_id

    @pytest.mark.asyncio
    async def test_engine_stop(self):
        config = BacktestConfig(
            strategy_name="ma_crossover", ws_port=0,
            strategy_params={"fast_period": 3, "slow_period": 5},
        )
        engine = BacktestEngine(config)
        feed = self._make_feed(100000)  # كثير من التيكات
        engine.stop()  # إيقاف فوري
        result = await engine.run(feed)
        # إما stopped early أو completed — المهم ما انفجر
        assert result.status in ("completed", "failed")


# ═══════════════════════════════════════════════════════════════════════════════
# اختبار مصادر البيانات
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataFeed:
    @pytest.mark.asyncio
    async def test_generate_synthetic(self):
        from backtest.data_feed import generate_synthetic_data
        feed = await generate_synthetic_data(symbol="EURUSD", num_ticks=500)
        assert feed.total_ticks == 500
        assert len(feed.candles) > 0
        assert feed.ticks[0].symbol == "EURUSD"

    @pytest.mark.asyncio
    async def test_load_from_json_file(self, tmp_path):
        from backtest.data_feed import load_from_file
        data = {
            "ticks": [
                {"symbol": "EURUSD", "ts": 1000, "bid": 1.085, "ask": 1.086, "volume": 100},
                {"symbol": "EURUSD", "ts": 1001, "bid": 1.086, "ask": 1.087, "volume": 200},
            ],
            "candles": [
                {"symbol": "EURUSD", "ts": 1000, "o": 1.085, "h": 1.087, "l": 1.084, "c": 1.086, "v": 300},
            ],
        }
        fp = tmp_path / "test_data.json"
        fp.write_text(json.dumps(data), encoding="utf-8")
        feed = await load_from_file(fp)
        assert feed.total_ticks == 2
        assert len(feed.candles) == 1

    @pytest.mark.asyncio
    async def test_load_from_csv(self, tmp_path):
        from backtest.data_feed import load_from_file
        csv = "timestamp,bid,ask,volume\n1000,1.085,1.086,100\n1001,1.086,1.087,200\n"
        fp = tmp_path / "test.csv"
        fp.write_text(csv, encoding="utf-8")
        feed = await load_from_file(fp)
        assert feed.total_ticks == 2

    @pytest.mark.asyncio
    async def test_load_nonexistent_file(self):
        from backtest.data_feed import load_from_file
        with pytest.raises(FileNotFoundError):
            await load_from_file("/nonexistent/path.json")

    @pytest.mark.asyncio
    async def test_unsupported_format(self, tmp_path):
        from backtest.data_feed import load_from_file
        fp = tmp_path / "test.xml"
        fp.write_text("<data></data>", encoding="utf-8")
        with pytest.raises(ValueError, match="غير مدعومة"):
            await load_from_file(fp)


# ═══════════════════════════════════════════════════════════════════════════════
# اختبار API
# ═══════════════════════════════════════════════════════════════════════════════

class TestAPI:
    def test_strategies_endpoint(self):
        from backtest.api import handle_request
        resp = handle_request("GET", "/backtest/strategies")
        assert "strategies" in resp
        assert len(resp["strategies"]) >= 3

    def test_status_idle(self):
        from backtest.api import handle_request
        resp = handle_request("GET", "/backtest/status")
        assert resp["status"] == "idle"

    def test_result_no_run(self):
        from backtest.api import handle_request
        resp = handle_request("GET", "/backtest/result")
        assert resp["status"] in ("no_result", "completed", "failed")

    def test_unknown_path(self):
        from backtest.api import handle_request
        resp = handle_request("GET", "/backtest/nonexistent")
        assert "error" in resp
