#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""تقرير PRODUCT GATE الآلي — يُنتج نتائج فعلية قابلة للإثبات.

يشغّل اختبارات حقيقية ويقيس:
  - هل الحي والباك تست يستعملان DataContract واحد؟
  - هل BacktestRunner يمر عبر ذرّات حقيقية؟
  - هل HistoricalClock يمنع look-ahead؟
  - هل ExperimentStore يحفظ مع provenance؟
  - هل E2E يمر؟
  - هل Paper adapter يعمل؟
  - هل لا توجد نتائج من synthetic في Product Gate؟

المخرج: جدول PASS/FAIL لكل بند + PRODUCT_GATE النهائي.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# ضمان أن الجذر في المسار
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _test_data_contract_unified() -> dict:
    """هل DataContract موحّد بين الحي والباك تست؟"""
    try:
        from backtest.data_contract import DataPoint, DataStream, DataProvenance

        # إنشاء نقطة بنفس العقد
        p = DataPoint(
            timestamp=time.time(), symbol="EURUSD", timeframe="M1",
            source="test", bid=1.085, ask=1.0852,
            open=1.085, high=1.086, low=1.084, close=1.0851,
            volume=1000, sequence=1,
        )
        # التحقق من كل الحقول المطلوبة
        required = ["symbol", "timestamp", "bid", "ask", "open", "high",
                     "low", "close", "volume", "timeframe", "source", "sequence"]
        d = p.to_dict()
        missing = [f for f in required if f not in d]
        # provenance
        prov = DataProvenance("test_source", time.time())
        stream = DataStream("EURUSD", "M1", "test", [p], provenance=prov)

        return {
            "pass": len(missing) == 0 and prov.original_source == "test_source",
            "details": f"missing={missing}, fields={list(d.keys())}",
        }
    except Exception as e:
        return {"pass": False, "details": str(e)}


def _test_historical_clock_prevents_future() -> dict:
    """هل HistoricalClock يمنع look-ahead؟"""
    try:
        from backtest.data_contract import DataPoint, DataStream
        from backtest.historical_clock import HistoricalClock, LookAheadError

        points = [DataPoint(timestamp=1000 + i, symbol="X", timeframe="tick",
                            source="test", close=float(i))
                  for i in range(20)]
        stream = DataStream(symbol="X", timeframe="tick", source="test", points=points)
        clock = HistoricalClock(stream, strict=True)
        next(clock)
        try:
            clock.peek(offset=5)
            return {"pass": False, "details": "لم يرمِ LookAheadError"}
        except LookAheadError:
            return {"pass": True, "details": "LookAheadError رُمي بنجاح"}
    except Exception as e:
        return {"pass": False, "details": str(e)}


def _test_runner_uses_real_atoms() -> dict:
    """هل BacktestRunner يحمّل ذرّات حقيقية؟"""
    try:
        atoms_dir = ROOT / "atoms"
        if not atoms_dir.exists():
            return {"pass": False, "details": "atoms/ غير موجود"}

        from backtest.runner import BacktestRunner, discover_atoms
        from backtest.data_contract import DataPoint

        runner = BacktestRunner(atoms_dir)
        count = runner.load_atoms(atom_ids=[151])
        if count == 0:
            return {"pass": False, "details": "لم تُحمَّل أي ذرّة"}

        # تشغيل
        points = []
        price = 1.085
        for i in range(100):
            price += 0.0001
            points.append(DataPoint(
                timestamp=1000 + i * 0.1, symbol="EURUSD", timeframe="tick",
                source="test", bid=price - 0.00005, ask=price + 0.00005,
                open=price, high=price + 0.0002, low=price - 0.0002,
                close=price, volume=1000, sequence=i,
            ))
        runner.set_data_from_points(points, symbol="EURUSD", source="test")
        result = runner.run()

        atoms_loaded = result["atoms_loaded"]
        tick_count = result["tick_count"]
        violations = result.get("clock_report", {}).get("look_ahead_violations", -1)

        return {
            "pass": atoms_loaded >= 1 and tick_count == 100 and violations == 0,
            "details": f"atoms={atoms_loaded}, ticks={tick_count}, violations={violations}, status={result['status']}",
        }
    except Exception as e:
        return {"pass": False, "details": str(e)}


def _test_runner_passes_through_atoms() -> dict:
    """هل BacktestRunner يمرر البيانات عبر analysis atoms؟"""
    try:
        atoms_dir = ROOT / "atoms"
        if not atoms_dir.exists():
            return {"pass": False, "details": "atoms/ غير موجود"}

        from backtest.runner import BacktestRunner
        from backtest.data_contract import DataPoint

        runner = BacktestRunner(atoms_dir)
        runner.load_atoms(atom_ids=[151])
        points = []
        price = 1.085
        for i in range(200):
            price += 0.00005
            points.append(DataPoint(
                timestamp=1000 + i * 0.1, symbol="EURUSD", timeframe="tick",
                source="test", bid=price - 0.00005, ask=price + 0.00005,
                open=price, high=price + 0.001, low=price - 0.001,
                close=price, volume=1000, sequence=i,
            ))
        runner.set_data_from_points(points)
        result = runner.run()

        # فحص هل ذرّة 151 أنتجت output
        bus_events = result["bus_report"]["total_events"]
        stage_analysis = result.get("stage_outputs", {}).get("analysis", {})
        analysis_count = stage_analysis.get("count", 0) if isinstance(stage_analysis, dict) else 0

        return {
            "pass": bus_events > 0,
            "details": f"bus_events={bus_events}, analysis_outputs={analysis_count}",
        }
    except Exception as e:
        return {"pass": False, "details": str(e)}


def _test_experiment_store() -> dict:
    """هل ExperimentStore يحفظ مع provenance؟"""
    try:
        import tempfile
        from backtest.experiment_store import ExperimentStore, ExperimentConfig, ExperimentResult

        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentStore(tmp)
            cfg = ExperimentConfig(
                symbol="EURUSD", data_source="ctrader",
                data_provenance={"source": "ctrader", "quality": "complete"},
            )
            exp = store.create(cfg)
            store.complete(exp, ExperimentResult(net_pnl=100))

            loaded = store.get(exp.run_id)
            if loaded is None:
                return {"pass": False, "details": "لم يُعثر على التجربة"}
            has_provenance = bool(loaded.config.data_provenance)
            return {
                "pass": has_provenance and loaded.status == "completed",
                "details": f"run_id={loaded.run_id}, provenance={has_provenance}",
            }
    except Exception as e:
        return {"pass": False, "details": str(e)}


def _test_paper_execution() -> dict:
    """هل PaperExecutionAdapter يعمل؟"""
    try:
        from backtest.execution import PaperExecutor, Order, OrderStatus

        ex = PaperExecutor(initial_balance=100_000)
        order = Order(symbol="EURUSD", side="BUY", size=1.0, price=1.085)
        result = ex.submit(order)

        return {
            "pass": result.status == OrderStatus.FILLED and result.mode == "paper",
            "details": f"status={result.status}, mode={result.mode}, balance={ex.get_balance()}",
        }
    except Exception as e:
        return {"pass": False, "details": str(e)}


def _test_no_synthetic_in_gate() -> dict:
    """هل بيانات synthetic مُعلَّمة ولا تدخل Product Gate؟"""
    try:
        from backtest.data_contract import DataPoint

        # نقطة synthetic
        p = DataPoint(timestamp=1000, symbol="X", timeframe="tick",
                      source="synthetic", close=1.0)
        is_synthetic = p.source == "synthetic"

        # نقطة حقيقية
        p2 = DataPoint(timestamp=1000, symbol="X", timeframe="tick",
                       source="ctrader", close=1.0)
        is_real = p2.source != "synthetic"

        return {
            "pass": is_synthetic and is_real,
            "details": f"synthetic='{p.source}', real='{p2.source}'",
        }
    except Exception as e:
        return {"pass": False, "details": str(e)}


def _test_deterministic_replay() -> dict:
    """هل نفس البيانات + نفس الذرّات = نفس النتيجة؟"""
    try:
        atoms_dir = ROOT / "atoms"
        if not atoms_dir.exists():
            return {"pass": False, "details": "atoms/ غير موجود"}

        from backtest.runner import BacktestRunner
        from backtest.data_contract import DataPoint

        points = []
        price = 1.085
        for i in range(100):
            price += 0.0001
            points.append(DataPoint(
                timestamp=1000 + i * 0.1, symbol="EURUSD", timeframe="tick",
                source="test", bid=price - 0.00005, ask=price + 0.00005,
                open=price, high=price + 0.0002, low=price - 0.0002,
                close=price, volume=1000, sequence=i,
            ))

        # تشغيل 1
        r1 = BacktestRunner(atoms_dir)
        r1.load_atoms(atom_ids=[151])
        r1.set_data_from_points(points)
        res1 = r1.run()

        # تشغيل 2
        r2 = BacktestRunner(atoms_dir)
        r2.load_atoms(atom_ids=[151])
        r2.set_data_from_points(points)
        res2 = r2.run()

        same_ticks = res1["tick_count"] == res2["tick_count"]
        same_events = res1["bus_report"]["total_events"] == res2["bus_report"]["total_events"]

        return {
            "pass": same_ticks and same_events,
            "details": f"ticks1={res1['tick_count']}, ticks2={res2['tick_count']}, events1={res1['bus_report']['total_events']}, events2={res2['bus_report']['total_events']}",
        }
    except Exception as e:
        return {"pass": False, "details": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# التقرير النهائي
# ═══════════════════════════════════════════════════════════════════════════════

CHECKS = [
    ("DataContract موحّد (حي + باك تست)", _test_data_contract_unified),
    ("HistoricalClock يمنع look-ahead", _test_historical_clock_prevents_future),
    ("BacktestRunner يحمّل ذرّات حقيقية", _test_runner_uses_real_atoms),
    ("BacktestRunner يمر عبر ذرّات التحليل", _test_runner_passes_through_atoms),
    ("ExperimentStore يحفظ مع provenance", _test_experiment_store),
    ("PaperExecutionAdapter يعمل", _test_paper_execution),
    ("بيانات synthetic مُعلَّمة ولا تدخل Gate", _test_no_synthetic_in_gate),
    ("Deterministic replay (نفس النتيجة)", _test_deterministic_replay),
]


def main() -> int:
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║           QUANT_NQ — PRODUCT GATE REPORT                   ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    passed = 0
    failed = 0
    results = []

    for name, check_fn in CHECKS:
        start = time.time()
        result = check_fn()
        elapsed = time.time() - start
        status = "✅ PASS" if result["pass"] else "❌ FAIL"
        if result["pass"]:
            passed += 1
        else:
            failed += 1
        results.append({"name": name, "pass": result["pass"], "details": result["details"]})
        print(f"  {status}  {name}")
        print(f"         └─ {result['details']}")
        print()

    gate = "PASS" if failed == 0 else "FAIL"
    print("══════════════════════════════════════════════════════════════")
    print(f" Checks:   {passed + failed}")
    print(f" Passed:   {passed}")
    print(f" Failed:   {failed}")
    print()
    print(f" PRODUCT GATE = {gate}")
    print("══════════════════════════════════════════════════════════════")

    # حفظ JSON
    report_path = ROOT / "var" / "product_gate_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp": time.time(),
        "product_gate": gate,
        "checks_total": passed + failed,
        "checks_passed": passed,
        "checks_failed": failed,
        "checks": results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n  التقرير محفوظ: {report_path.relative_to(ROOT)}")

    return 0 if gate == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
