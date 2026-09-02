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
    "_atom621", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom621"] = _mod
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


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    ctx = AtomContext(atom_id=621,
                      config={"symbol_map": {"DXY": "DX-Y.NYB", "US500": "^GSPC"}},
                      logger=_NullLogger(), publish=bus.publish,
                      subscribe=bus.subscribe)
    await atom.initialize(ctx)
    await atom.start()
    return atom, bus


def _refs(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_mapped_tick_becomes_reference():
    print("\n--- test_mapped_tick_becomes_reference ---")
    atom, bus = await _new()
    await atom._on_tick({"s": "DXY", "bid": 104.21, "ask": 104.23,
                         "price": 104.22, "ts": 1700000000000, "sequence": 5})
    refs = _refs(bus)
    assert len(refs) == 1, "تكة رمز مرجعي يجب أن تتحول لمرجع"
    assert refs[0] == {"provider": "ctrader", "symbol": "DX-Y.NYB",
                       "value": 104.22}, refs[0]
    print("OK — DXY من الوسيط صار DX-Y.NYB بنفس عقد ياهو")


async def test_unmapped_symbol_ignored():
    print("\n--- test_unmapped_symbol_ignored ---")
    atom, bus = await _new()
    await atom._on_tick({"s": "BTCUSD", "bid": 64000.0, "ask": 64001.0,
                         "price": 64000.5})
    assert _refs(bus) == [], "رمز غير مرجعي يجب ألا يتحول لمرجع"
    print("OK — بيتكوين ليس مؤشرًا مرجعيًا")


async def test_bad_price_ignored():
    print("\n--- test_bad_price_ignored ---")
    atom, bus = await _new()
    await atom._on_tick({"s": "US500", "bid": 0, "ask": -1, "price": None})
    assert _refs(bus) == [], "سعر فاسد لا يُمرَّر"
    print("OK — سعر فاسد لا يصير مرجعًا")


async def test_health_ready_then_active():
    print("\n--- test_health_ready_then_active ---")
    atom, bus = await _new()
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY and "READY" in h.message, h.message
    await atom._on_tick({"s": "DXY", "bid": 104.0, "ask": 104.1, "price": 104.05})
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY and "forwarded=1" in h2.message
    print("OK — جاهزٌ سليم قبل التدفق، وعدّاده يظهر بعده")


async def main():
    tests = [test_mapped_tick_becomes_reference, test_unmapped_symbol_ignored,
             test_bad_price_ignored, test_health_ready_then_active]
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
