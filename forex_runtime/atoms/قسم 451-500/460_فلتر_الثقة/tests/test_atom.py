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
    "_atom460", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom460"] = _mod
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
        return AtomContext(atom_id=460, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _conf(signal):
    return {"symbol": "NQ100", "timeframe": "60s", "cycle_id": "NQ100|60s|0.0",
            "signal": signal}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _last(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


async def test_high_pass():
    print("\n--- test_high_pass ---")
    atom, bus = await _new()
    await atom._on_input(_conf("high_confidence"))
    last = _last(bus)
    assert last["signal"] == "pass" and last["metadata"]["passed"] is True
    print("OK — high → pass")


async def test_low_block():
    print("\n--- test_low_block ---")
    atom, bus = await _new()
    await atom._on_input(_conf("low_confidence"))
    last = _last(bus)
    assert last["signal"] == "block" and last["metadata"]["passed"] is False
    print("OK — low → block")


async def test_health_contract():
    print("\n--- test_health_contract ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    await atom._on_input(_conf("high_confidence"))
    assert _last(bus)["id"] == "confidence_filter"
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — العقد + الصحة")


async def main():
    tests = [test_high_pass, test_low_block, test_health_contract]
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
