# -*- coding: utf-8 -*-
"""اختبار تسميم المستقبل — Lookahead Poison Test.

المرحلة 6 من ورقة الإغلاق.

الفكرة:
  ١. شغّل حتى T
  ٢. غيّر كل البيانات بعد T
  ٣. شغّل حتى T مرة أخرى
  ٤. النتيجة يجب أن تكون متطابقة تمامًا

أي اختلاف = LOOKAHEAD = FAIL
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from backtest.data_contract import DataPoint, DataProvenance, DataStream
from backtest.historical_clock import HistoricalClock, LookAheadError


def _make_stream(prices: list[float], start_ts: float = 1000.0, step: float = 60.0) -> DataStream:
    """بناء DataStream من قائمة أسعار."""
    points = []
    for i, price in enumerate(prices):
        points.append(DataPoint(
            timestamp=start_ts + i * step,
            symbol="TEST", timeframe="M1", source="test",
            open=price, high=price + 0.5, low=price - 0.5, close=price,
            volume=1000.0,
        ))
    return DataStream(
        symbol="TEST", timeframe="M1", source="test",
        points=points,
        provenance=DataProvenance(
            original_source="test",
            ingest_time=time.time(),
            transformations=[],
        ),
    )


def _run_until(stream: DataStream, target_time: float) -> tuple[list[float], int]:
    """شغّل HistoricalClock حتى target_time. يرجع (الأسعار المُسلَّمة, عدد النقاط)."""
    clock = HistoricalClock(stream, strict=True)
    delivered: list[float] = []
    for point in clock:
        if point.timestamp > target_time:
            break
        delivered.append(point.close)
    return delivered, clock.state.points_delivered


def test_lookahead_poison_same_result():
    """اختبار التسميم: تسميم المستقبل لا يغيّر النتيجة.

    ١. شغّل حتى T
    ٢. غيّر كل البيانات بعد T (أسعار مختلفة تمامًا)
    ٣. شغّل حتى T مرة أخرى
    ٤. النتيجة يجب أن تكون متطابقة تمامًا
    """
    # البيانات الأصلية: 20 نقطة
    original_prices = [100.0 + i * 0.1 for i in range(20)]
    stream_original = _make_stream(original_prices)

    # T = النقطة 10 (timestamp = 1000 + 10*60 = 1600)
    T = 1000.0 + 10 * 60.0

    # تشغيل حتى T
    result_1, count_1 = _run_until(stream_original, T)

    # تسميم: غيّر كل البيانات بعد T
    poisoned_prices = original_prices.copy()
    for i in range(11, 20):
        poisoned_prices[i] = 999.0 + i  # أسعار مختلفة تمامًا
    stream_poisoned = _make_stream(poisoned_prices)

    # تشغيل حتى T مرة أخرى
    result_2, count_2 = _run_until(stream_poisoned, T)

    # النتيجة يجب أن تكون متطابقة
    assert result_1 == result_2, (
        f"LOOKAHEAD DETECTED! "
        f"قبل التسميم: {result_1}, "
        f"بعد التسميم: {result_2}"
    )
    assert count_1 == count_2, (
        f"LOOKAHEAD: عدد النقاط مختلف: {count_1} vs {count_2}"
    )
    print(f"  ✓ {len(result_1)} نقطة — متطابقة قبل وبعد التسميم")


def test_peek_future_raises():
    """peek(offset>0) يرمي LookAheadError."""
    stream = _make_stream([100.0 + i for i in range(10)])
    clock = HistoricalClock(stream, strict=True)

    # تقدم نقطة واحدة
    next(clock)
    # حاول النظر للمستقبل
    try:
        clock.peek(offset=1)
        assert False, "يجب أن يرمي LookAheadError"
    except LookAheadError:
        pass
    print("  ✓ peek(offset>0) يرمي LookAheadError")


def test_visible_window_no_future():
    """visible_window لا يعطي بيانات مستقبلية."""
    stream = _make_stream([100.0 + i for i in range(10)])
    clock = HistoricalClock(stream, strict=True)

    # تقدم 3 نقاط
    for _ in range(3):
        next(clock)

    window = clock.visible_window(5)
    assert len(window) <= 3, (
        f"نافذة مرئية فيها {len(window)} نقاط — "
        f"لكن تقدمنا 3 فقط"
    )
    # كل نقاط النافذة ≤ fence
    for point in window:
        assert point.timestamp <= clock._fence, (
            f"نقطة في النافذة المرئية بعد fence! "
            f"{point.timestamp} > {clock._fence}"
        )
    print(f"  ✓ نافذة مرئية: {len(window)} نقاط — لا مستقبل")


def test_fence_enforcement():
    """fence يمنع أي تجاوز."""
    stream = _make_stream([100.0 + i for i in range(10)])
    clock = HistoricalClock(stream, strict=True)

    # تقدم 5 نقاط
    for _ in range(5):
        next(clock)

    assert clock._fence == 1000.0 + 4 * 60.0, (
        f"fence = {clock._fence} — يجب أن يكون {1000.0 + 4 * 60.0}"
    )
    print(f"  ✓ fence = {clock._fence}")


def test_poison_with_different_stream_lengths():
    """تسميم يعمل حتى مع أطوال مختلفة للبيانات."""
    # Stream قصير
    short_prices = [100.0 + i * 0.5 for i in range(5)]
    stream_short = _make_stream(short_prices)

    T = 1000.0 + 3 * 60.0  # بعد النقطة 3
    result_1, count_1 = _run_until(stream_short, T)

    # Stream أطول مع تسميم
    long_prices = [100.0 + i * 0.5 for i in range(5)] + [999.0] * 50
    stream_long = _make_stream(long_prices)
    result_2, count_2 = _run_until(stream_long, T)

    assert result_1 == result_2, f"LOOKAHEAD: {result_1} != {result_2}"
    assert count_1 == count_2
    print(f"  ✓ أطوال مختلفة — نفس النتيجة ({count_1} نقطة)")


_ALL_TESTS = [
    test_lookahead_poison_same_result,
    test_peek_future_raises,
    test_visible_window_no_future,
    test_fence_enforcement,
    test_poison_with_different_stream_lengths,
]


def run() -> int:
    passed = 0
    failed = 0
    for test in _ALL_TESTS:
        try:
            test()
            passed += 1
            print(f"✓ {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"✗ {test.__name__}: {exc}")
    print(f"\n{'='*50}")
    print(f"المرحلة 6 — Lookahead Poison: {passed} نجح · {failed} فشل")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(run())
