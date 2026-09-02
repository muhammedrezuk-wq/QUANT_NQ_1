import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom158", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom158"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

_HOUR = 3600.0


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
        return AtomContext(atom_id=158, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"utc_offset_hours": 0, "asia_start": 0, "asia_end": 9,
       "london_start": 7, "london_end": 16, "ny_start": 12, "ny_end": 21,
       "crypto_symbols": ["BTCUSD"], "always_open_symbols": ["XAUUSD"]}


def _candle(hour, symbol="NQ100", tf="60s"):
    ts = hour * _HOUR
    return {"symbol": symbol, "period_start": ts, "timestamp": ts, "timeframe": tf}


async def _emit_hour(hour):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle(_candle(hour))
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


async def test_asia():
    print("\n--- test_asia ---")
    o = await _emit_hour(3)  # 03:00 UTC → آسيا فقط
    assert o["signal"] == "asia", o["signal"]
    print(f"OK — آسيا: hour={o['metadata']['hour_utc']}")


async def test_london():
    print("\n--- test_london ---")
    o = await _emit_hour(10)  # 10:00 UTC → لندن فقط
    assert o["signal"] == "london", o["signal"]
    print(f"OK — لندن: hour={o['metadata']['hour_utc']}")


async def test_overlap():
    print("\n--- test_overlap ---")
    o = await _emit_hour(13)  # 13:00 UTC → لندن + نيويورك
    assert o["signal"] == "overlap", o["signal"]
    assert set(o["metadata"]["active"]) == {"london", "new_york"}
    print(f"OK — تداخل: active={o['metadata']['active']} score={o['score']}")


async def test_closed():
    print("\n--- test_closed ---")
    o = await _emit_hour(22)  # 22:00 UTC → لا جلسة
    assert o["signal"] == "closed", o["signal"]
    print("OK — مغلق: closed")


async def test_crypto_24h():
    print("\n--- test_crypto_24h ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle(_candle(22, symbol="BTCUSD"))  # فجوة جلسات → كريبتو مفتوح
    o = [p for n, p in bus.published if n == EVENT_OUT][-1]
    assert o["signal"] == "crypto_24h", o["signal"]
    assert o["score"] > 0, o["score"]
    print(f"OK — كريبتو 24/7: signal={o['signal']} score={o['score']}")


async def test_always_open():
    print("\n--- test_always_open ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle(_candle(22, symbol="XAUUSD"))  # فجوة → ذهب مفتوح 24/5
    o = [p for n, p in bus.published if n == EVENT_OUT][-1]
    assert o["signal"] == "open", o["signal"]
    assert o["score"] > 0, o["score"]
    print(f"OK — مفتوح 24/5 (ذهب): signal={o['signal']} score={o['score']}")


async def test_contract_shape():
    print("\n--- test_contract_shape ---")
    o = await _emit_hour(13)
    for f in ("symbol", "id", "cycle_id", "status", "signal", "score",
              "confidence", "quality", "warnings", "metadata"):
        assert f in o, f"حقل ناقص: {f}"
    for f in ("method", "hour_utc", "active"):
        assert f in o["metadata"], f"metadata ناقص: {f}"
    assert o["id"] == "session"
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_candle(_candle(10))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_no_state_to_survive():
    print("\n--- test_no_state_to_survive ---")
    # أول شمعة بعد الإقلاع مباشرة — لا نافذة إحماء هنا أصلًا لتُفقد.
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    await atom._on_candle(_candle(13))
    out = [p for n, p in bus.published if n == EVENT_OUT][-1]
    assert out["status"] == "ok", out["status"]
    saved = await atom.snapshot()
    assert "live_analysis" in saved, "الغلاف الحيّ لازم يبقى محفوظًا"
    assert "atom" not in saved, "لا حالة خاصة بالذرّة لتُحفظ — كل نتيجة دالّة نقية"
    print("OK — لا حالة تُفقد: أول شمعة بعد الإقلاع تصدر نتيجة كاملة")


async def main():
    tests = [test_asia, test_london, test_overlap, test_closed,
             test_crypto_24h, test_always_open, test_contract_shape,
             test_health_states, test_no_state_to_survive]
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
