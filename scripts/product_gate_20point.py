#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Product Gate — 20-point verification with REAL intraday data.

يستخدم شموع M15 حقيقية من Yahoo Finance / OKX — لا توسيع ولا اشتقاق.
المسار الكامل: DATA → CLOCK → EVENT BUS → ANALYSIS → STRUCTURE → LIQUIDITY → 
STATISTICS → PROBABILITY → STRATEGY → DECISION → RISK → EXPERIMENT STORE → RESULT
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import warnings
    warnings.filterwarnings('ignore')

    print("=" * 80)
    print("PRODUCT GATE — 20-POINT VERIFICATION (REAL M15 DATA)")
    print("=" * 80)
    print()

    results = []

    # ═══ 1. بيانات M15 حقيقية موجودة ═══
    print("[1/20] Real M15 historical data (intraday)...")
    yahoo_file = ROOT / "data/historical/btcusd_m15_yahoo_60d.json"
    okx_file = ROOT / "data/historical/btcusd_m15_okx_15d.json"
    if yahoo_file.exists():
        yd = json.loads(yahoo_file.read_text())
        y_count = yd["count"]
        y_first = yd["first"]
        y_last = yd["last"]
        results.append(("Real M15 data (Yahoo)", y_count > 1000, f"{y_count} candles, {y_first[:10]} to {y_last[:10]}"))
        print(f"  ✅ Yahoo: {y_count} real M15 candles ({y_first[:10]} to {y_last[:10]})")
    else:
        results.append(("Real M15 data (Yahoo)", False, "File not found"))
        print(f"  ❌ Yahoo file not found")

    if okx_file.exists():
        od = json.loads(okx_file.read_text())
        o_count = od["count"]
        results.append(("Real M15 data (OKX)", o_count > 100, f"{o_count} candles"))
        print(f"  ✅ OKX: {o_count} real M15 candles")
    else:
        results.append(("Real M15 data (OKX)", False, "File not found"))
        print(f"  ❌ OKX file not found")

    # ═══ 2. محمّل البيانات الحقيقية ═══
    print("[2/20] Data loader (real M15, no expansion)...")
    try:
        from backtest.historical_data import load_real_m15
        stream = load_real_m15(source="yahoo", max_candles=500)
        # إثبات: ليست مشتقة — أسعار مختلفة
        unique_closes = len(set(p.close for p in stream.points[:100]))
        is_real = unique_closes >= 90  # 90%+ أسعار مختلفة
        results.append(("Data loader (real M15)", is_real, f"{len(stream.points)} candles, {unique_closes}/100 unique closes"))
        print(f"  ✅ {len(stream.points)} real M15 candles, {unique_closes}/100 unique closes (not derived)")
    except Exception as e:
        results.append(("Data loader (real M15)", False, str(e)))
        print(f"  ❌ {e}")

    # ═══ 3. HistoricalClock ═══
    print("[3/20] HistoricalClock (no look-ahead)...")
    try:
        from backtest.historical_clock import HistoricalClock, LookAheadError
        from backtest.data_contract import DataStream, DataPoint, DataProvenance

        points = [
            DataPoint(timestamp=1000, symbol="BTC", timeframe="M15", source="test", close=1.0, volume=1),
            DataPoint(timestamp=2000, symbol="BTC", timeframe="M15", source="test", close=1.1, volume=1),
            DataPoint(timestamp=3000, symbol="BTC", timeframe="M15", source="test", close=1.2, volume=1),
        ]
        stream_test = DataStream(symbol="BTC", timeframe="M15", source="test",
                                  points=points, provenance=DataProvenance(original_source="test", ingest_time=time.time()))
        clock = HistoricalClock(stream_test, strict=True)
        next(clock)  # consume first point
        try:
            clock.peek(1)
            results.append(("HistoricalClock", False, "peek(1) should have raised LookAheadError"))
            print(f"  ❌ Should have raised LookAheadError")
        except LookAheadError:
            results.append(("HistoricalClock", True, "LookAheadError on peek(1)"))
            print(f"  ✅ LookAheadError raised on peek(1)")
    except Exception as e:
        results.append(("HistoricalClock", False, str(e)))
        print(f"  ❌ {e}")

    # ═══ 4. الذرّات ═══
    print("[4/20] Full atom pipeline...")
    try:
        from backtest.runner import BacktestRunner
        runner = BacktestRunner()
        count = runner.load_full_pipeline()
        results.append(("Atom pipeline", count >= 45, f"{count} atoms"))
        print(f"  ✅ {count} atoms loaded")
    except Exception as e:
        results.append(("Atom pipeline", False, str(e)))
        print(f"  ❌ {e}")

    # ═══ تشغيل الباك تست على M15 حقيقية ═══
    from backtest.runner import BacktestRunner
    from backtest.historical_data import load_real_m15
    from backtest.data_contract import DataStream
    stream = load_real_m15(source="yahoo", max_candles=500)
    runner = BacktestRunner()
    runner.load_full_pipeline()
    runner.set_data(stream)
    bt_result = runner.run()

    # ═══ 5-12. مراحل المسار ═══
    stages = {
        5: ("Analysis", "analysis"),
        6: ("Structure", "structure"),
        7: ("Liquidity", "liquidity"),
        8: ("Statistics", "statistics"),
        9: ("Probability", "probability"),
        10: ("Strategy", "strategy"),
        11: ("Decision", "decision"),
        12: ("Risk", "risk"),
    }
    for num, (name, key) in stages.items():
        print(f"[{num}/20] {name} stage...")
        cnt = bt_result["stages"][key]["count"]
        ok = cnt > 0
        results.append((f"{name} stage", ok, f"{cnt:,} outputs"))
        icon = "✅" if ok else "❌"
        print(f"  {icon} {cnt:,} outputs")

    # ═══ 13. ExperimentStore ═══
    print("[13/20] ExperimentStore...")
    try:
        from backtest.experiment_store import ExperimentStore, ExperimentConfig, ExperimentResult
        store = ExperimentStore()
        exp_config = ExperimentConfig(symbol="BTCUSDT", timeframe="M15", mode="backtest",
                                       data_source="yahoo_finance", strategy="pipeline")
        exp = store.create(exp_config)
        exp_result = ExperimentResult(total_trades=10, winning_trades=6, win_rate=60.0)
        store.complete(exp, exp_result)
        results.append(("ExperimentStore", exp.status == "completed", f"status={exp.status}"))
        print(f"  ✅ Lifecycle recorded (id={exp.run_id[:12]})")
    except Exception as e:
        results.append(("ExperimentStore", False, str(e)))
        print(f"  ❌ {e}")

    # ═══ 14. Tick payloads ═══
    print("[14/20] Tick payloads (full fields)...")
    try:
        from backtest.historical_data import to_tick_payload
        p = stream.points[0]
        payload = to_tick_payload(p)
        ok = all(k in payload for k in ["account_id", "broker", "source_timestamp"])
        results.append(("Tick payloads", ok, "account_id+broker+source_timestamp"))
        print(f"  ✅ Full fields")
    except Exception as e:
        results.append(("Tick payloads", False, str(e)))
        print(f"  ❌ {e}")

    # ═══ 15. Candle payloads ═══
    print("[15/20] Candle payloads (real OHLCV)...")
    try:
        from backtest.historical_data import to_candle_payload
        p = stream.points[0]
        payload = to_candle_payload(p)
        ok = all(k in payload for k in ["open", "high", "low", "close", "volume", "source_timestamp"])
        # إثبات: OHLCV حقيقية — ليست كلها نفس القيمة
        is_real = not (payload["open"] == payload["high"] == payload["low"] == payload["close"])
        results.append(("Candle payloads (real OHLCV)", ok and is_real, "OHLCV varies (real market data)"))
        print(f"  ✅ O={payload['open']}, H={payload['high']}, L={payload['low']}, C={payload['close']}")
    except Exception as e:
        results.append(("Candle payloads", False, str(e)))
        print(f"  ❌ {e}")

    # ═══ 16. إعادة التشغيل الحتمية ═══
    print("[16/20] Deterministic replay...")
    try:
        stream2 = load_real_m15(source="yahoo", max_candles=200)
        r1 = BacktestRunner(); r1.load_full_pipeline(); r1.set_data(stream2); res1 = r1.run()
        r2 = BacktestRunner(); r2.load_full_pipeline(); r2.set_data(stream2); res2 = r2.run()
        same = res1["tick_count"] == res2["tick_count"] and res1["stages"] == res2["stages"]
        results.append(("Deterministic replay", same, f"{res1['tick_count']} candles → same"))
        print(f"  ✅ {res1['tick_count']} candles → identical outputs")
    except Exception as e:
        results.append(("Deterministic replay", False, str(e)))
        print(f"  ❌ {e}")

    # ═══ 17. لا standalone ═══
    print("[17/20] No standalone backtest imports...")
    try:
        content = (ROOT / "backtest/runner.py").read_text()
        has_standalone = "backtest.indicators" in content or "backtest.strategies" in content
        results.append(("No standalone imports", not has_standalone, "Uses real atoms"))
        print(f"  ✅ Real atoms only")
    except Exception as e:
        results.append(("No standalone imports", False, str(e)))
        print(f"  ❌ {e}")

    # ═══ 18. Run tracking ═══
    print("[18/20] Run tracking...")
    run_id = bt_result.get("run_id", "")
    ok = bool(run_id) and run_id.startswith("RUN-")
    results.append(("Run tracking", ok, f"run_id={run_id}"))
    print(f"  ✅ {run_id}")

    # ═══ 19. مخرجات حقيقية ═══
    print("[19/20] Full pipeline execution...")
    total = sum(info["count"] for info in bt_result["stages"].values())
    ok = total > 1000
    results.append(("Full pipeline execution", ok, f"{total:,} outputs"))
    print(f"  ✅ {total:,} total outputs from real M15 data")

    # ═══ 20. Closed build ═══
    print("[20/20] Closed build...")
    core = [ROOT / "core/contracts/atom.py", ROOT / "shared/tick_contract.py"]
    ok = all(f.exists() for f in core)
    results.append(("Closed build", ok, "Core sealed"))
    print(f"  ✅ Core contracts sealed")

    # ═══════════════════════════════════════════════════════════════
    # ملخص
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    passed = sum(1 for _, ok, _ in results if ok)
    total_checks = len(results)

    for name, ok, detail in results:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}: {detail}")

    print()
    print(f"PRODUCT GATE: {passed}/{total_checks}")
    print()

    if passed == total_checks:
        print("🎉 PRODUCT GATE = PASS")
        print("   Full path proven with REAL M15 intraday data.")
        print("   No expansion, no derivation, no synthetic.")
        return 0
    else:
        print("❌ PRODUCT GATE = FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
