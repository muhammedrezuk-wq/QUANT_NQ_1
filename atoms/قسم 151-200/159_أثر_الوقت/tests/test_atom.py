import asyncio
import os
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom159", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom159"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT


class _NullLogger:
    def debug(self, *a): pass
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass
    def critical(self, *a): pass


class FakeEventBus:
    def __init__(self):
        self.published = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=159, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"utc_offset_hours": 0, "week_open_day": 0, "week_close_day": 4}


def _ts(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def _candle(ts, symbol="USTECm", tf="60s"):
    return {"symbol": symbol, "period_start": ts, "timestamp": ts, "timeframe": tf}


async def _emit(ts):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle(_candle(ts))
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


async def test_week_open():
    print("\n--- test_week_open ---")
    o = await _emit(_ts(2024, 1, 1))  # 2024-01-01 اثنين
    assert o["signal"] == "week_open", o["signal"]
    assert o["metadata"]["month"] == 1 and o["metadata"]["quarter"] == 1
    assert o["metadata"]["is_month_start"] is True
    print(f"OK — فتح الأسبوع: {o['metadata']['weekday_name']} score={o['score']}")


async def test_week_close():
    print("\n--- test_week_close ---")
    o = await _emit(_ts(2024, 1, 5))  # 2024-01-05 جمعة
    assert o["signal"] == "week_close", o["signal"]
    print(f"OK — إغلاق الأسبوع: {o['metadata']['weekday_name']}")


async def test_mid_week():
    print("\n--- test_mid_week ---")
    o = await _emit(_ts(2024, 1, 3))  # 2024-01-03 أربعاء
    assert o["signal"] == "mid_week", o["signal"]
    assert o["metadata"]["weekday_name"] == "wednesday"
    print("OK — وسط الأسبوع: wednesday")


async def test_quarter_end():
    print("\n--- test_quarter_end ---")
    o = await _emit(_ts(2024, 3, 31))  # نهاية Q1
    assert o["metadata"]["is_month_end"] is True
    assert o["metadata"]["is_quarter_end"] is True
    assert o["metadata"]["quarter"] == 1
    print("OK — نهاية الربع: is_quarter_end")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    o = await _emit(_ts(2024, 1, 3))
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in o, f"حقل ناقص: {f}"
    for f in ("method", "hour", "weekday", "weekday_name", "month", "quarter",
              "day_of_month", "is_month_start", "is_month_end", "is_quarter_end"):
        assert f in o["metadata"], f"metadata ناقص: {f}"
    assert o["id"] == "time"
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(_ts(2024, 1, 3)))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_no_state_to_survive():
    print("\n--- test_no_state_to_survive ---")
    # أول شمعة بعد الإقلاع مباشرة — لا نافذة إحماء هنا أصلًا لتُفقد.
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle(_candle(_ts(2024, 1, 3)))
    out = [p for n, p in bus.published if n == EVENT_OUT][-1]
    assert out["status"] == "ok", out["status"]
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" not in saved, "لا حالة خاصة بالذرّة لتُحفظ — كل نتيجة دالّة نقية"
    print("OK — لا حالة تُفقد: أول شمعة بعد الإقلاع تصدر نتيجة كاملة")


async def main():
    tests = [test_week_open, test_week_close, test_mid_week, test_quarter_end,
             test_contract_shape, test_health_states, test_no_state_to_survive]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
