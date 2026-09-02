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
    "_atom307", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom307"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

REF = "USTEC"
SYM = "XAUUSD"
CFG = {"reference_symbol": REF, "window": 30,
       "strong_threshold": 0.7, "moderate_threshold": 0.4}


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
        return AtomContext(atom_id=307, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tick(price, sequence, symbol, timeframe="60s"):
    return {"symbol": symbol, "price": price, "volume": 1, "timeframe": "tick",
            "timestamp": sequence, "sequence": sequence}


def _series(rets, start=100.0):
    closes = [start]
    for r in rets:
        closes.append(closes[-1] * (1.0 + r))
    return closes


async def _run(ref_rets, sym_rets, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    ref_closes = _series(ref_rets)
    sym_closes = _series(sym_rets)
    for i in range(len(ref_closes)):
        await atom._on_tick(_tick(ref_closes[i], float(i), REF))
        await atom._on_tick(_tick(sym_closes[i], float(i), SYM))
    sym_out = [p for n, p in bus.published if n == EVENT_OUT and p["symbol"] == SYM]
    ref_out = [p for n, p in bus.published if n == EVENT_OUT and p["symbol"] == REF]
    return atom, sym_out, ref_out


_RETS = [0.01, -0.006, 0.008, -0.003, 0.011, -0.009, 0.004, 0.007,
         -0.005, 0.006, -0.002, 0.009]


async def test_strong_positive():
    print("\n--- test_strong_positive ---")
    _atom, sym_out, _ref = await _run(_RETS, list(_RETS))
    last = sym_out[-1]
    assert last["signal"] == "strong", last["signal"]
    assert last["metadata"]["direction"] == "positive", last["metadata"]["direction"]
    assert last["metadata"]["value"] > 0.9, last["metadata"]["value"]
    print(f"OK — عوائد متطابقة: strong positive (r={last['metadata']['value']})")


async def test_strong_inverse():
    print("\n--- test_strong_inverse ---")
    inv = [-r for r in _RETS]
    _atom, sym_out, _ref = await _run(_RETS, inv)
    last = sym_out[-1]
    assert last["signal"] == "strong", last["signal"]
    assert last["metadata"]["direction"] == "inverse", last["metadata"]["direction"]
    assert last["metadata"]["value"] < -0.9, last["metadata"]["value"]
    print(f"OK — عوائد معاكسة: strong inverse (r={last['metadata']['value']})")


async def test_insufficient_overlap():
    print("\n--- test_insufficient_overlap ---")
    _atom, sym_out, _ref = await _run(_RETS[:3], list(_RETS[:3]))
    assert sym_out[-1]["status"] == "insufficient_data", sym_out[-1]["status"]
    print("OK — تداخل ناقص: insufficient")


async def test_reference_signal():
    print("\n--- test_reference_signal ---")
    _atom, _sym, ref_out = await _run(_RETS, list(_RETS))
    non_insuf = [p for p in ref_out if p["status"] == "ok"]
    assert non_insuf, "لا مخرج ok للمرجع"
    assert non_insuf[-1]["signal"] == "reference", non_insuf[-1]["signal"]
    print("OK — الرمز المرجعي: reference")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _atom, sym_out, _ref = await _run(_RETS, list(_RETS))
    last = sym_out[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص: {field}"
    for field in ("value", "direction", "reference", "points"):
        assert field in last["metadata"], f"حقل metadata ناقص: {field}"
    assert last["id"] == "correlation"
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    await atom._on_tick(_tick(100.0, 0.0, REF))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_strong_positive, test_strong_inverse, test_insufficient_overlap,
             test_reference_signal, test_contract_shape_complete, test_health_states]
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
