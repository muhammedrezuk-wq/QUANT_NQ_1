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
    "_atom256", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom256"] = _mod
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
        self._handlers = {}

    def subscribe(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=256, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _trade(buy=None, sell=None, symbol="NQ100", tf="60s"):
    p = {"symbol": symbol, "cycle_id": "c", "metadata": {"timeframe": tf}}
    if buy is not None:
        p["buy_volume"] = buy
    if sell is not None:
        p["sell_volume"] = sell
    return p


async def _mk():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _out(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_unavailable_by_default():
    print("\n--- test_unavailable_by_default ---")
    atom, _bus = await _mk()
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED
    assert h.message == "ORDER_FLOW_UNAVAILABLE"
    print("OK — بلا تدفّق حقيقي: DEGRADED · ORDER_FLOW_UNAVAILABLE (صادق)")


async def test_no_fabrication_without_volume():
    print("\n--- test_no_fabrication_without_volume ---")
    atom, bus = await _mk()
    await atom._on_trade(_trade())  # لا حجوم بيع/شراء
    assert not _out(bus), "بلا حجم حقيقي = لا نشر (ما نخترع دلتا)"
    print("OK — بلا حجم اتجاهي: لا فبركة، لا نشر")


async def test_computes_with_real_volume():
    print("\n--- test_computes_with_real_volume ---")
    atom, bus = await _mk()
    await atom._on_trade(_trade(buy=100, sell=60))  # تدفّق حقيقي (مسار المستقبل)
    last = _out(bus)[-1]
    assert last["signal"] == "buy_pressure", last["signal"]
    assert last["metadata"]["delta"] == 40
    assert "score" not in last, "لا حقل score ميت — §12"
    print(f"OK — عند تدفّق حقيقي: يحسب delta={last['metadata']['delta']} → buy_pressure")


async def test_healthy_when_flow_present():
    print("\n--- test_healthy_when_flow_present ---")
    atom, _bus = await _mk()
    await atom._on_trade(_trade(buy=100, sell=60))
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY
    print("OK — عند وصول التدفّق: HEALTHY")


async def test_lifecycle_before_start():
    print("\n--- test_lifecycle_before_start ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    h = await atom.health_check()
    assert h.state == HealthState.UNHEALTHY
    print("OK — قبل start: UNHEALTHY")


async def main():
    tests = [
        test_unavailable_by_default,
        test_no_fabrication_without_volume,
        test_computes_with_real_volume,
        test_healthy_when_flow_present,
        test_lifecycle_before_start,
    ]
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
