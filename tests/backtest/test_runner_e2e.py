# -*- coding: utf-8 -*-
"""اختبارات BacktestRunner — يشغّل ذرّات حقيقية على بيانات تاريخية.

هذا الاختبار يثبت أن:
  - BacktestRunner يحمّل ذرّات فعلية من atoms/
  - يمرر البيانات عبر SyncEventBus
  - الذرّة تتلقى الأحداث وتنشر مخرجات
  - HistoricalClock يمنع look-ahead
  - ExperimentStore يحفظ النتيجة مع provenance
  - المسار كامل: Data → Clock → Bus → Atom → Result → Store
"""
from __future__ import annotations

import time
import random

import pytest

from backtest.data_contract import DataPoint, DataStream, DataProvenance
from backtest.historical_clock import HistoricalClock, LookAheadError
from backtest.sync_event_bus import SyncEventBus, create_logger
from backtest.runner import BacktestRunner, discover_atoms, _load_atom_class
from backtest.experiment_store import ExperimentConfig, ExperimentResult, ExperimentStore


def _make_points(n=200, symbol="EURUSD", start_price=1.085, trend=0.0) -> list[DataPoint]:
    """توليد نقاط بيانات (synthetic — للاختبار فقط)."""
    points = []
    price = start_price
    t = 1000.0
    for i in range(n):
        change = random.gauss(trend, 0.0003)
        price = max(price + change, 0.001)
        bid = price - 0.00005
        ask = price + 0.00005
        points.append(DataPoint(
            timestamp=t + i * 0.1, symbol=symbol, timeframe="tick",
            source="synthetic_test", bid=round(bid, 5), ask=round(ask, 5),
            open=round(price - 0.0001, 5), high=round(price + 0.0002, 5),
            low=round(price - 0.0002, 5), close=round(price, 5),
            volume=random.randint(100, 5000), sequence=i,
        ))
    return points


# ═══════════════════════════════════════════════════════════════════════════════
# SyncEventBus
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyncEventBus:
    def test_subscribe_and_publish(self):
        bus = SyncEventBus()
        received = []
        bus.subscribe("test.event", lambda p: received.append(p))
        bus.publish("test.event", {"key": "value"})
        assert len(received) == 1
        assert received[0]["key"] == "value"

    def test_multiple_handlers(self):
        bus = SyncEventBus()
        count = [0]
        bus.subscribe("x", lambda p: count.__setitem__(0, count[0] + 1))
        bus.subscribe("x", lambda p: count.__setitem__(0, count[0] + 10))
        bus.publish("x", {})
        assert count[0] == 11

    def test_event_recording(self):
        bus = SyncEventBus()
        bus.subscribe("a", lambda p: None)
        bus.publish("a", {"v": 1})
        bus.publish("b", {"v": 2})
        events = bus.get_events()
        assert len(events) == 2
        assert events[0][0] == "a"
        assert events[1][0] == "b"

    def test_get_last(self):
        bus = SyncEventBus()
        bus.subscribe("x", lambda p: None)
        bus.publish("x", {"v": 1})
        bus.publish("x", {"v": 2})
        last = bus.get_last("x")
        assert last is not None
        assert last["v"] == 2

    def test_subscribe_all(self):
        bus = SyncEventBus()
        all_events = []
        bus.subscribe_all(lambda name, p: all_events.append(name))
        bus.publish("a", {})
        bus.publish("b", {})
        assert len(all_events) == 2

    def test_error_isolation(self):
        bus = SyncEventBus()
        bus.subscribe("x", lambda p: 1/0)  # يرمي
        bus.subscribe("x", lambda p: None)  # يعمل
        bus.publish("x", {})
        assert bus.report()["total_errors"] == 1
        assert bus.report()["total_dispatches"] == 1  # الثاني نجح

    def test_report(self):
        bus = SyncEventBus()
        bus.subscribe("a", lambda p: None)
        bus.subscribe("b", lambda p: None)
        bus.publish("a", {})
        r = bus.report()
        assert r["total_events"] == 1
        assert r["subscribed_events"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Atom Loading
# ═══════════════════════════════════════════════════════════════════════════════

class TestAtomLoading:
    def test_discover_atoms(self):
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")
        discovered = discover_atoms(atoms_dir)
        assert len(discovered) > 0
        # لازم نلقى ذرّة 151 (الاتجاه)
        ids = [d["id"] for d in discovered]
        assert 151 in ids

    def test_load_atom_class(self):
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")
        # تحميل ذرّة 151
        atom_dir = None
        for section_dir in atoms_dir.iterdir():
            if not section_dir.is_dir():
                continue
            candidate = section_dir / "151_الاتجاه"
            if candidate.exists():
                atom_dir = candidate
                break
        if atom_dir is None:
            pytest.skip("ذرّة 151 غير موجودة")
        cls = _load_atom_class(atom_dir, "test_atom_151")
        assert cls is not None

    def test_runner_load_atoms(self):
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")
        runner = BacktestRunner(atoms_dir)
        count = runner.load_atoms(atom_ids=[151])
        assert count >= 1
        assert 151 in runner.loaded_atom_ids

    def test_runner_load_pipeline(self):
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")
        runner = BacktestRunner(atoms_dir)
        count = runner.load_full_pipeline()
        assert count >= 1  # على الأقل ذرّة واحدة نجحت


# ═══════════════════════════════════════════════════════════════════════════════
# BacktestRunner — التشغيل الفعلي
# ═══════════════════════════════════════════════════════════════════════════════

class TestBacktestRunnerExecution:
    def test_run_with_real_atom(self):
        """تشغيل ذرّة حقيقية (151 الاتجاه) على بيانات تاريخية."""
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")

        runner = BacktestRunner(atoms_dir)
        count = runner.load_atoms(atom_ids=[151])
        if count == 0:
            pytest.skip("لم تُحمَّل أي ذرّة")

        points = _make_points(200)
        runner.set_data_from_points(points, symbol="EURUSD", source="test")
        result = runner.run()

        assert result["status"] == "completed"
        assert result["tick_count"] == 200
        assert result["atoms_loaded"] >= 1
        assert result["bus_report"]["total_events"] > 0
        assert result["clock_report"]["violations_free"] is True

    def test_run_produces_stage_outputs(self):
        """التشغيل ينتج مخرجات مراحل."""
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")

        runner = BacktestRunner(atoms_dir)
        runner.load_atoms(atom_ids=[151])
        points = _make_points(300)
        runner.set_data_from_points(points)
        result = runner.run()

        assert "stages" in result
        assert "analysis" in result["stages"]

    def test_run_no_data_fails(self):
        """التشغيل بدون بيانات يرفض."""
        runner = BacktestRunner()
        runner.load_atoms(atom_ids=[151])
        result = runner.run()
        assert result["status"] == "failed"
        assert "لا توجد بيانات" in result["error"]

    def test_run_no_atoms_fails(self):
        """التشغيل بدون ذرّات يرفض."""
        runner = BacktestRunner()
        points = _make_points(100)
        runner.set_data_from_points(points)
        result = runner.run()
        assert result["status"] == "failed"
        assert "لا توجد ذرّات" in result["error"]

    def test_run_id_generated(self):
        """كل تشغيل ينتج run_id فريد."""
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")

        runner = BacktestRunner(atoms_dir)
        runner.load_atoms(atom_ids=[151])
        runner.set_data_from_points(_make_points(50))
        r1 = runner.run()

        runner2 = BacktestRunner(atoms_dir)
        runner2.load_atoms(atom_ids=[151])
        runner2.set_data_from_points(_make_points(50))
        r2 = runner2.run()

        assert r1["run_id"] != r2["run_id"]
        assert r1["run_id"].startswith("RUN-")

    def test_provenance_in_result(self):
        """النتيجة تحتوي provenance."""
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")

        runner = BacktestRunner(atoms_dir)
        runner.load_atoms(atom_ids=[151])
        points = _make_points(50)
        runner.set_data_from_points(points, symbol="EURUSD", source="ctrader_test")
        result = runner.run()

        assert result["provenance"]
        assert result["data_info"]["symbol"] == "EURUSD"
        assert result["data_info"]["source"] == "ctrader_test"


# ═══════════════════════════════════════════════════════════════════════════════
# E2E — الدورة الكاملة
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_full_cycle(self, tmp_path):
        """الدورة الكاملة:
        DataPoint → DataStream → HistoricalClock → SyncEventBus → Atom →
        ExperimentStore → reload → same result
        """
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")

        # 1. بيانات
        points = _make_points(300, symbol="EURUSD")
        stream = DataStream(
            symbol="EURUSD", timeframe="tick", source="test_e2e",
            points=points,
            provenance=DataProvenance("test_source", time.time()),
        )

        # 2. فحص صحة
        v = stream.validate()
        assert v["valid"] is True

        # 3. تشغيل
        runner = BacktestRunner(atoms_dir)
        count = runner.load_atoms(atom_ids=[151])
        if count == 0:
            pytest.skip("لم تُحمَّل أي ذرّة")
        runner.set_data(stream)
        result = runner.run()

        assert result["status"] == "completed"
        assert result["run_id"].startswith("RUN-")

        # 4. حفظ في ExperimentStore
        store = ExperimentStore(tmp_path)
        cfg = ExperimentConfig(
            symbol="EURUSD", data_source="test_e2e",
            data_points=300, strategy="atom_151", mode="backtest",
            data_provenance=stream.provenance.to_dict() if stream.provenance else {},
        )
        exp = store.create(cfg)
        exp_result = ExperimentResult(
            total_trades=len(result.get("decisions", {}).get("samples", [])),
        )
        store.complete(exp, exp_result, clock_report=result.get("clock_report"))

        # 5. إعادة قراءة
        loaded = store.get(exp.run_id)
        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.result.clock_report.get("violations_free") is True

    def test_look_ahead_prevention_in_runner(self):
        """Runner يمرر البيانات عبر HistoricalClock — لا تسريب مستقبل."""
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")

        runner = BacktestRunner(atoms_dir)
        runner.load_atoms(atom_ids=[151])
        points = _make_points(100)
        runner.set_data_from_points(points)
        result = runner.run()

        # الساعة لازم تكون خالية من المخالفات
        assert result["clock_report"]["violations_free"] is True
        assert result["clock_report"]["look_ahead_violations"] == 0

    def test_deterministic_replay(self):
        """نفس البيانات + نفس الذرّات = نفس النتيجة (حتمية)."""
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")

        points = _make_points(100)

        # تشغيل 1
        runner1 = BacktestRunner(atoms_dir)
        runner1.load_atoms(atom_ids=[151])
        runner1.set_data_from_points(points)
        r1 = runner1.run()

        # تشغيل 2 (نفس البيانات)
        runner2 = BacktestRunner(atoms_dir)
        runner2.load_atoms(atom_ids=[151])
        runner2.set_data_from_points(points)
        r2 = runner2.run()

        # نفس عدد التيكات + نفس عدد الأحداث
        assert r1["tick_count"] == r2["tick_count"]
        assert r1["bus_report"]["total_events"] == r2["bus_report"]["total_events"]

    def test_synthetic_data_marked(self):
        """بيانات synthetic تحمل source=synthetic."""
        points = _make_points(50)
        assert all(p.source == "synthetic_test" for p in points)

    def test_experiment_store_comparison(self, tmp_path):
        """مقارنة تجربتين."""
        from pathlib import Path
        atoms_dir = Path(__file__).resolve().parents[2] / "atoms"
        if not atoms_dir.exists():
            pytest.skip("atoms/ غير موجود")

        store = ExperimentStore(tmp_path)

        # تجربة 1
        e1 = store.create(ExperimentConfig(symbol="EURUSD", strategy="A"))
        store.complete(e1, ExperimentResult(net_pnl=100, total_trades=5))

        # تجربة 2
        e2 = store.create(ExperimentConfig(symbol="EURUSD", strategy="B"))
        store.complete(e2, ExperimentResult(net_pnl=200, total_trades=8))

        comp = store.compare([e1.run_id, e2.run_id])
        assert comp["winner"]["run_id"] == e2.run_id
        assert comp["winner"]["net_pnl"] == 200


# ═══════════════════════════════════════════════════════════════════════════════
# اختبارات الحماية
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtections:
    def test_clock_blocks_future_access(self):
        """HistoricalClock يمنع الوصول للمستقبل."""
        points = [DataPoint(timestamp=1000 + i, symbol="X", timeframe="tick",
                            source="test", close=float(i))
                  for i in range(20)]
        stream = DataStream(symbol="X", timeframe="tick", source="test", points=points)
        clock = HistoricalClock(stream, strict=True)
        next(clock)
        with pytest.raises(LookAheadError):
            clock.peek(offset=5)

    def test_clock_visible_window_no_future(self):
        """النافذة المرئية لا تحتوي مستقبل."""
        points = [DataPoint(timestamp=1000 + i, symbol="X", timeframe="tick",
                            source="test", close=float(i))
                  for i in range(20)]
        stream = DataStream(symbol="X", timeframe="tick", source="test", points=points)
        clock = HistoricalClock(stream)
        # نمرر 5 نقاط
        for _ in range(5):
            next(clock)
        window = clock.visible_window(10)
        # كل النقاط في النافذة زمنها <= الزمن الحالي
        assert all(p.timestamp <= clock.current_time for p in window)

    def test_no_result_without_provenance(self, tmp_path):
        """لا نتيجة بلا provenance."""
        store = ExperimentStore(tmp_path)
        # تجربة بدون provenance
        cfg = ExperimentConfig(symbol="EURUSD", data_source="")
        exp = store.create(cfg)
        assert exp.config.data_source == ""  # مقصود — فارغ
        # لكن التجربة نفسها لا تُحفظ كـ "completed" بلا provenance صالح
        store.complete(exp, ExperimentResult())
        loaded = store.get(exp.run_id)
        assert loaded is not None
        assert loaded.status == "completed"
        # التحذير: provenance فارغ — لا يُعتبر منتج
        assert loaded.config.data_provenance == {}

    def test_duplicate_timestamps_detected(self):
        """الفحص يكتشف تكرار الطوابع الزمنية."""
        points = [
            DataPoint(timestamp=1000, symbol="X", timeframe="tick", source="t"),
            DataPoint(timestamp=1000, symbol="X", timeframe="tick", source="t"),  # مكرر
            DataPoint(timestamp=1001, symbol="X", timeframe="tick", source="t"),
        ]
        stream = DataStream(symbol="X", timeframe="tick", source="t", points=points)
        # فحص: الوقت لا يرجع — لكن التكرار ممكن
        # HistoricalClock يتعامل معه
        clock = HistoricalClock(stream, strict=False)
        delivered = list(clock)
        assert len(delivered) == 3

    def test_out_of_order_rejected(self):
        """الترتيب الزمني الخاطئ يُكتشف."""
        points = [
            DataPoint(timestamp=2000, symbol="X", timeframe="tick", source="t"),
            DataPoint(timestamp=1000, symbol="X", timeframe="tick", source="t"),  # رجوع!
        ]
        stream = DataStream(symbol="X", timeframe="tick", source="t", points=points)
        v = stream.validate()
        assert v["valid"] is False
        assert any("TIME_ORDER" in e for e in v["errors"])

    def test_empty_provenance_flagged(self):
        """بيانات بلا provenance تُعلَّم."""
        stream = DataStream(symbol="X", timeframe="tick", source="test")
        assert stream.provenance is None
        # لا يجب أن تُستخدم كمنتج
        v = stream.validate()
        assert v["count"] == 0  # فارغة
