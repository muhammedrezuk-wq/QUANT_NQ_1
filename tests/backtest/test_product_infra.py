# -*- coding: utf-8 -*-
"""اختبارات البنية التحتية للمنتج — DataContract + Clock + Experiments + Execution."""
from __future__ import annotations

import json
import time

import pytest

from backtest.data_contract import (
    DataPoint, DataProvenance, DataStream, DataQuality, DataSource,
    TimeFrame, create_stream_from_candles,
)
from backtest.historical_clock import HistoricalClock, LookAheadError
from backtest.experiment_store import (
    Experiment, ExperimentConfig, ExperimentResult, ExperimentStore,
)
from backtest.execution import (
    BacktestExecutor, PaperExecutor, LiveExecutor, Order, OrderStatus,
    create_executor, ExecutionMode,
)


# ═══════════════════════════════════════════════════════════════════════════════
# DataPoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataPoint:
    def test_basic_creation(self):
        p = DataPoint(timestamp=1000, symbol="EURUSD", timeframe="M1",
                      source="ctrader", open=1.085, high=1.086, low=1.084,
                      close=1.0855, volume=1000)
        assert p.symbol == "EURUSD"
        assert p.close == 1.0855

    def test_mid_price(self):
        p = DataPoint(timestamp=0, symbol="X", timeframe="tick",
                      source="test", bid=1.0850, ask=1.0852)
        assert p.mid == pytest.approx(1.0851)

    def test_spread(self):
        p = DataPoint(timestamp=0, symbol="X", timeframe="tick",
                      source="test", bid=1.0850, ask=1.0852)
        assert p.spread == pytest.approx(0.0002)

    def test_fingerprint_unique(self):
        p1 = DataPoint(timestamp=1000, symbol="X", timeframe="M1", source="a", sequence=1)
        p2 = DataPoint(timestamp=1000, symbol="X", timeframe="M1", source="a", sequence=2)
        assert p1.fingerprint() != p2.fingerprint()

    def test_fingerprint_deterministic(self):
        p = DataPoint(timestamp=1000, symbol="X", timeframe="M1", source="a", sequence=1)
        assert p.fingerprint() == p.fingerprint()

    def test_to_dict(self):
        p = DataPoint(timestamp=1000, symbol="X", timeframe="M1", source="test")
        d = p.to_dict()
        assert d["symbol"] == "X"
        assert d["timestamp"] == 1000


# ═══════════════════════════════════════════════════════════════════════════════
# DataStream
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataStream:
    def _make_stream(self, n=100) -> DataStream:
        points = [DataPoint(timestamp=1000 + i * 60, symbol="EURUSD",
                            timeframe="M1", source="test",
                            open=1.085 + i * 0.0001, high=1.086 + i * 0.0001,
                            low=1.084 + i * 0.0001, close=1.085 + i * 0.0001,
                            volume=1000)
                  for i in range(n)]
        return DataStream(symbol="EURUSD", timeframe="M1", source="test",
                          points=points)

    def test_length(self):
        s = self._make_stream(50)
        assert len(s) == 50

    def test_first_last_ts(self):
        s = self._make_stream(10)
        assert s.first_ts == 1000
        assert s.last_ts == 1000 + 9 * 60

    def test_validate_valid_stream(self):
        s = self._make_stream(100)
        v = s.validate()
        assert v["valid"] is True
        assert v["errors"] == []

    def test_validate_empty_stream(self):
        s = DataStream(symbol="X", timeframe="M1", source="test")
        v = s.validate()
        assert v["valid"] is False
        assert "EMPTY_STREAM" in v["errors"]

    def test_validate_time_order_violation(self):
        points = [
            DataPoint(timestamp=2000, symbol="X", timeframe="M1", source="t"),
            DataPoint(timestamp=1000, symbol="X", timeframe="M1", source="t"),  # رجوع!
        ]
        s = DataStream(symbol="X", timeframe="M1", source="t", points=points)
        v = s.validate()
        assert v["valid"] is False
        assert any("TIME_ORDER" in e for e in v["errors"])

    def test_detect_gaps(self):
        points = [
            DataPoint(timestamp=1000, symbol="X", timeframe="M1", source="t"),
            DataPoint(timestamp=1060, symbol="X", timeframe="M1", source="t"),
            DataPoint(timestamp=1120, symbol="X", timeframe="M1", source="t"),
            DataPoint(timestamp=1600, symbol="X", timeframe="M1", source="t"),  # فجوة!
            DataPoint(timestamp=1660, symbol="X", timeframe="M1", source="t"),
        ]
        s = DataStream(symbol="X", timeframe="M1", source="t", points=points)
        gaps = s.detect_gaps(60)
        assert len(gaps) == 1

    def test_iter(self):
        s = self._make_stream(5)
        count = sum(1 for _ in s)
        assert count == 5


# ═══════════════════════════════════════════════════════════════════════════════
# HistoricalClock
# ═══════════════════════════════════════════════════════════════════════════════

class TestHistoricalClock:
    def _make_clock(self, n=100) -> HistoricalClock:
        points = [DataPoint(timestamp=1000 + i * 60, symbol="EURUSD",
                            timeframe="M1", source="test",
                            close=1.085 + i * 0.0001)
                  for i in range(n)]
        stream = DataStream(symbol="EURUSD", timeframe="M1", source="test",
                            points=points)
        return HistoricalClock(stream)

    def test_iteration(self):
        clock = self._make_clock(10)
        delivered = list(clock)
        assert len(delivered) == 10
        assert clock.state.points_delivered == 10

    def test_time_moves_forward(self):
        clock = self._make_clock(10)
        times = []
        for p in clock:
            times.append(p.timestamp)
        assert times == sorted(times)

    def test_no_look_ahead_via_peek(self):
        clock = self._make_clock(10)
        next(clock)  # تسلّم أول نقطة
        # محاولة نظر للمستقبل
        with pytest.raises(LookAheadError):
            clock.peek(offset=1)

    def test_look_ahead_violation_counted(self):
        # strict=False — يسجّل المخالفة بدون رمي
        clock = self._make_clock(10)
        clock._strict = False
        next(clock)
        clock.peek(offset=0)  # مسموح
        assert clock.violations == 0
        clock.peek(offset=1)  # مخالفة!
        assert clock.violations == 1

    def test_visible_window(self):
        clock = self._make_clock(10)
        for _ in range(5):
            next(clock)
        window = clock.visible_window(3)
        assert len(window) == 3
        # آخر نقطة في النافذة = آخر نقطة مُسلَّمة
        assert window[-1].timestamp == 1000 + 4 * 60

    def test_stop_and_resume(self):
        clock = self._make_clock(10)
        for _ in range(3):
            next(clock)
        clock.stop()
        with pytest.raises(StopIteration):
            next(clock)
        clock.resume()
        p = next(clock)
        assert p.timestamp == 1000 + 3 * 60  # النقطة الرابعة

    def test_save_and_restore(self):
        clock = self._make_clock(10)
        for _ in range(5):
            next(clock)
        saved = clock.save_state()
        clock2 = self._make_clock(10)
        clock2.restore_state(saved)
        assert clock2.position == 5
        assert clock2.current_time == 1000 + 4 * 60
        # تكمل من حيث وقفت
        p = next(clock2)
        assert p.timestamp == 1000 + 5 * 60

    def test_report(self):
        clock = self._make_clock(10)
        for _ in clock:
            pass
        r = clock.report()
        assert r["completed"] is True
        assert r["points_delivered"] == 10
        assert r["violations_free"] is True

    def test_advance_to(self):
        clock = self._make_clock(100)
        target = 1000 + 10 * 60
        batch = clock.advance_to(target)
        assert len(batch) == 11  # 0..10
        assert batch[-1].timestamp <= target


# ═══════════════════════════════════════════════════════════════════════════════
# ExperimentStore
# ═══════════════════════════════════════════════════════════════════════════════

class TestExperimentStore:
    def test_create_and_get(self, tmp_path):
        store = ExperimentStore(tmp_path)
        cfg = ExperimentConfig(symbol="EURUSD", strategy="ma_crossover")
        exp = store.create(cfg)
        assert exp.run_id.startswith("RUN-")
        assert exp.status == "created"
        # قراءة
        loaded = store.get(exp.run_id)
        assert loaded is not None
        assert loaded.config.symbol == "EURUSD"

    def test_complete(self, tmp_path):
        store = ExperimentStore(tmp_path)
        exp = store.create(ExperimentConfig(symbol="EURUSD"))
        result = ExperimentResult(total_trades=10, net_pnl=500, win_rate=0.6)
        store.complete(exp, result)
        assert exp.status == "completed"
        assert exp.result.net_pnl == 500

    def test_fail(self, tmp_path):
        store = ExperimentStore(tmp_path)
        exp = store.create(ExperimentConfig())
        store.fail(exp, "data error")
        assert exp.status == "failed"
        assert exp.error == "data error"

    def test_list_all(self, tmp_path):
        store = ExperimentStore(tmp_path)
        for i in range(5):
            store.create(ExperimentConfig(symbol=f"SYM{i}"))
        lst = store.list_all()
        assert len(lst) == 5

    def test_compare(self, tmp_path):
        store = ExperimentStore(tmp_path)
        e1 = store.create(ExperimentConfig(strategy="A"))
        store.complete(e1, ExperimentResult(net_pnl=100))
        e2 = store.create(ExperimentConfig(strategy="B"))
        store.complete(e2, ExperimentResult(net_pnl=200))
        comp = store.compare([e1.run_id, e2.run_id])
        assert comp["experiments"] == 2
        assert comp["winner"]["run_id"] == e2.run_id

    def test_persistence(self, tmp_path):
        store = ExperimentStore(tmp_path)
        exp = store.create(ExperimentConfig(symbol="EURUSD", strategy="rsi"))
        store.complete(exp, ExperimentResult(net_pnl=1000))
        # إنشاء store جديد من نفس المجلد
        store2 = ExperimentStore(tmp_path)
        loaded = store2.get(exp.run_id)
        assert loaded is not None
        assert loaded.result.net_pnl == 1000

    def test_delete(self, tmp_path):
        store = ExperimentStore(tmp_path)
        exp = store.create(ExperimentConfig())
        assert store.delete(exp.run_id) is True
        assert store.get(exp.run_id) is None


# ═══════════════════════════════════════════════════════════════════════════════
# Execution Adapters
# ═══════════════════════════════════════════════════════════════════════════════

class TestBacktestExecutor:
    def test_submit_fill(self):
        ex = BacktestExecutor()
        order = Order(symbol="EURUSD", side="BUY", size=1.0, price=1.085)
        result = ex.submit(order)
        assert result.status == OrderStatus.FILLED
        assert result.fill_price > 0

    def test_commission(self):
        ex = BacktestExecutor(commission_per_unit=5.0)
        order = Order(symbol="X", side="BUY", size=2.0, price=1.0)
        ex.submit(order)
        assert order.commission == 10.0

    def test_slippage_buy(self):
        ex = BacktestExecutor(slippage_pips=1.0)
        order = Order(symbol="X", side="BUY", size=1.0, price=1.085)
        ex.submit(order)
        assert order.fill_price > order.price

    def test_slippage_sell(self):
        ex = BacktestExecutor(slippage_pips=1.0)
        order = Order(symbol="X", side="SELL", size=1.0, price=1.085)
        ex.submit(order)
        assert order.fill_price < order.price

    def test_balance_decreases_with_commission(self):
        ex = BacktestExecutor(commission_per_unit=10, initial_balance=1000)
        order = Order(symbol="X", side="BUY", size=1.0, price=1.0)
        ex.submit(order)
        assert ex.get_balance() == 990.0

    def test_position_tracking(self):
        ex = BacktestExecutor()
        order = Order(symbol="X", side="BUY", size=1.0, price=1.0)
        ex.submit(order)
        assert len(ex.get_positions()) == 1

    def test_close_position(self):
        ex = BacktestExecutor(initial_balance=1000)
        order = Order(symbol="X", side="BUY", size=1.0, price=1.0)
        ex.submit(order)
        pid = ex.get_positions()[0]["id"]
        result = ex.close_position(pid, 1.1)
        assert result is not None
        assert result["pnl"] == pytest.approx(0.1)


class TestPaperExecutor:
    def test_submit(self):
        ex = PaperExecutor()
        order = Order(symbol="X", side="BUY", size=1.0, price=1.0)
        result = ex.submit(order)
        assert result.status == OrderStatus.FILLED
        assert result.mode == "paper"

    def test_cancel(self):
        ex = PaperExecutor()
        order = Order(id="PP-001", symbol="X", side="BUY", size=1.0, price=1.0,
                      status=OrderStatus.SUBMITTED)
        ex._orders.append(order)
        assert ex.cancel("PP-001") is True


class TestLiveExecutor:
    def test_not_connected(self):
        ex = LiveExecutor()
        order = Order(symbol="X", side="BUY", size=1.0, price=1.0)
        result = ex.submit(order)
        assert result.status == OrderStatus.REJECTED
        assert "NOT_CONNECTED" in result.reject_reason

    def test_create_executor_factory(self):
        bt = create_executor("backtest")
        assert isinstance(bt, BacktestExecutor)
        pp = create_executor("paper")
        assert isinstance(pp, PaperExecutor)
        lv = create_executor("live")
        assert isinstance(lv, LiveExecutor)

    def test_unknown_mode(self):
        with pytest.raises(ValueError):
            create_executor("unknown")


# ═══════════════════════════════════════════════════════════════════════════════
# E2E — الدورة الكاملة
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    """الدورة الكاملة: بيانات → ساعة → تنفيذ → نتيجة → حفظ → إعادة قراءة."""

    def test_full_cycle(self, tmp_path):
        # 1. بيانات
        points = [DataPoint(timestamp=1000 + i * 60, symbol="EURUSD",
                            timeframe="M1", source="ctrader",
                            open=1.085 + i * 0.0001, high=1.086 + i * 0.0001,
                            low=1.084 + i * 0.0001, close=1.085 + i * 0.0001,
                            volume=1000)
                  for i in range(100)]
        stream = DataStream(symbol="EURUSD", timeframe="M1", source="ctrader",
                            points=points,
                            provenance=DataProvenance("ctrader", time.time()))

        # 2. فحص صحة
        v = stream.validate()
        assert v["valid"] is True

        # 3. ساعة تاريخية
        clock = HistoricalClock(stream)
        consumed = []
        for p in clock:
            consumed.append(p)
        assert len(consumed) == 100
        assert clock.report()["violations_free"] is True

        # 4. تنفيذ
        executor = BacktestExecutor(initial_balance=100000)
        order = Order(symbol="EURUSD", side="BUY", size=1.0,
                      price=consumed[50].close)
        executor.submit(order)
        assert len(executor.get_positions()) == 1

        # 5. إغلاق + نتيجة
        pid = executor.get_positions()[0]["id"]
        close_result = executor.close_position(pid, consumed[-1].close)
        assert close_result is not None

        # 6. حفظ التجربة
        store = ExperimentStore(tmp_path)
        cfg = ExperimentConfig(
            symbol="EURUSD", timeframe="M1", data_source="ctrader",
            data_points=100, strategy="manual", mode="backtest",
            data_provenance=stream.provenance.to_dict() if stream.provenance else {},
        )
        exp = store.create(cfg)
        result = ExperimentResult(
            total_trades=1, net_pnl=close_result["pnl"],
            final_equity=executor.get_balance(),
            clock_report=clock.report(),
        )
        store.complete(exp, result)

        # 7. إعادة قراءة
        loaded = store.get(exp.run_id)
        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.result.total_trades == 1
        assert loaded.result.clock_report["violations_free"] is True
        assert loaded.config.data_provenance["original_source"] == "ctrader"

    def test_backtest_vs_paper_same_input(self, tmp_path):
        """نفس البيانات + نفس الأمر → نتائج متطابقة (إلا السلبج)."""
        points = [DataPoint(timestamp=1000 + i * 60, symbol="EURUSD",
                            timeframe="M1", source="test",
                            close=1.085, volume=1000)
                  for i in range(10)]
        stream = DataStream(symbol="EURUSD", timeframe="M1", source="test",
                            points=points)

        # باك تست
        bt = BacktestExecutor(slippage_pips=0, commission_per_unit=0)
        order_bt = Order(symbol="EURUSD", side="BUY", size=1.0, price=1.085)
        bt.submit(order_bt)

        # Paper
        pp = PaperExecutor()
        order_pp = Order(symbol="EURUSD", side="BUY", size=1.0, price=1.085)
        pp.submit(order_pp)

        # نفس سعر الملء (بلا سلبج)
        assert order_bt.fill_price == order_pp.fill_price
        assert order_bt.fill_size == order_pp.fill_size

    def test_clock_prevents_future(self):
        """الساعة تمنع قراءة المستقبل — اختبار صريح."""
        points = [DataPoint(timestamp=1000 + i, symbol="X",
                            timeframe="tick", source="test", close=float(i))
                  for i in range(50)]
        stream = DataStream(symbol="X", timeframe="tick", source="test",
                            points=points)
        clock = HistoricalClock(stream, strict=True)

        next(clock)  # تسلّم النقطة 0
        # محاولة نظر 5 نقاط للأمام
        with pytest.raises(LookAheadError):
            clock.peek(offset=5)
        assert clock.violations == 1
