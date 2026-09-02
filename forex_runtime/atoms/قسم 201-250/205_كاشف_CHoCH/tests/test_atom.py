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
    "_atom205", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom205"] = _mod
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
        return AtomContext(atom_id=205, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _ext(sh=None, sh_t=None, sl=None, sl_t=None, close=0.0, ts=0.0, symbol="NQ100", tf="60s"):
    return {"symbol": symbol, "id": "external", "cycle_id": "%s|%s|%s" % (symbol, tf, ts),
            "status": "ok", "signal": "none", "score": 0, "confidence": 0.0,
            "quality": "good", "warnings": [],
            "metadata": {"method": "swing_extension", "timeframe": tf,
                         "swing_high": sh, "swing_high_time": sh_t,
                         "swing_low": sl, "swing_low_time": sl_t, "close": close}}


async def _run(exts):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    for e in exts:
        await atom._on_external(e)
    choch = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, choch


async def test_first_break_not_choch():
    print("\n--- test_first_break_not_choch ---")
    _atom, _bus, ch = await _run([_ext(sh=10, sh_t=1, sl=5, sl_t=0, close=11, ts=1)])
    assert ch[-1]["signal"] == "none", "أول كسر ليس CHoCH"
    print("OK — أول كسر ليس CHoCH (بحقّ)")


async def test_reversal_is_choch():
    print("\n--- test_reversal_is_choch ---")
    _atom, _bus, ch = await _run([
        _ext(sh=10, sh_t=1, sl=5, sl_t=0, close=11, ts=1),   # break up
        _ext(sh=10, sh_t=1, sl=8, sl_t=2, close=7, ts=2)])   # break down → CHoCH
    last = ch[-1]
    assert last["signal"] == "choch", last["signal"]
    assert last["metadata"]["direction"] == "down"
    assert last["metadata"]["level"] == 8
    assert last["score"] > 0
    print(f"OK — انعكاس: CHoCH down · level={last['metadata']['level']} score={last['score']}")


async def test_continuation_not_choch():
    print("\n--- test_continuation_not_choch ---")
    _atom, _bus, ch = await _run([
        _ext(sh=10, sh_t=1, sl=5, sl_t=0, close=11, ts=1),   # break up
        _ext(sh=12, sh_t=2, sl=5, sl_t=0, close=13, ts=2)])  # break up → BOS
    assert ch[-1]["signal"] == "none", "الاستمرار ليس CHoCH (ذاك BOS)"
    print("OK — الاستمرار ليس CHoCH (شغل 204)")


async def test_no_break():
    print("\n--- test_no_break ---")
    _atom, _bus, ch = await _run([_ext(sh=10, sh_t=1, sl=5, sl_t=0, close=7, ts=1)])
    assert ch[-1]["signal"] == "none"
    print("OK — لا كسر: none")


async def test_single_break_dedup():
    print("\n--- test_single_break_dedup ---")
    atom, _bus, _ch = await _run([
        _ext(sh=10, sh_t=1, sl=5, sl_t=0, close=11, ts=1),
        _ext(sh=10, sh_t=1, sl=5, sl_t=0, close=12, ts=2)])
    assert atom._breaks == 1, f"الكسر يُحسب مرّة (طلع {atom._breaks})"
    print("OK — الكسر مرّة وحدة")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    await atom._on_external(_ext(sh=10, sh_t=1, sl=5, sl_t=0, close=7, ts=1))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_first_break_not_choch,
        test_reversal_is_choch,
        test_continuation_not_choch,
        test_no_break,
        test_single_break_dedup,
        test_health_states,
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
