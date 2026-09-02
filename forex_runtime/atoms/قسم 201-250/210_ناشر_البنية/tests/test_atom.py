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
    "_atom210", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom210"] = _mod
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
        return AtomContext(atom_id=210, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _u(uid, signal="none", status="ok", score=0, confidence=0.0, meta=None):
    return {"symbol": "NQ100", "id": uid, "cycle_id": "c1", "status": status,
            "signal": signal, "score": score, "confidence": confidence,
            "quality": "good", "warnings": [], "metadata": meta or {}}


def _validated(results, cycle="c1", symbol="NQ100"):
    return {"cycle_id": cycle, "symbol": symbol, "timeframe": "60s", "results": results,
            "expected": 8, "present": len(results), "complete": True, "validated": True}


def _full_results():
    # الإلزاميّات الأربع (swing/external/internal/structure_trend) حاضرة
    # كلّها هنا + بعض الاختياريّة (phase/mss) — هذا ما يجعلها "كاملة" فعلًا
    # منذ REQUIRED_UNITS["200"] (§٢٠)، لا العدّاد الخام expected=8 القديم.
    return {
        "structure_trend": _u("structure_trend", "uptrend", score=60, confidence=1.0,
                              meta={"timeframe": "60s", "confirmations": 3, "source": "mss"}),
        "phase": _u("phase", "established", score=60, confidence=0.6,
                    meta={"timeframe": "60s", "confirmations": 3}),
        "external": _u("external", "HH", confidence=0.9,
                       meta={"timeframe": "60s", "swing_high": 12, "swing_low": 8, "close": 13}),
        "swing": _u("swing", "swing_high", confidence=0.8,
                    meta={"timeframe": "60s", "price": 12, "close": 13}),
        "internal": _u("internal", "bullish", confidence=0.5,
                       meta={"timeframe": "60s"}),
        "mss": _u("mss", "shift",
                  meta={"timeframe": "60s", "shift_type": "bos", "direction": "up"}),
    }


async def _mk():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _out(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_assembles_snapshot():
    print("\n--- test_assembles_snapshot ---")
    atom, bus = await _mk()
    await atom._on_validated(_validated(_full_results()))
    last = _out(bus)[-1]
    assert last["signal"] == "uptrend", last["signal"]
    assert last["status"] == "ok"
    # الثقة = متوسط الإلزاميّات الأربع حصرًا (§٢١):
    # (structure_trend=1.0 + external=0.9 + swing=0.8 + internal=0.5) / 4 = 0.8
    # — لا يشمل phase/mss الاختياريّتين (كانتا تُخفّضان الثقة سابقًا).
    assert last["score"] == 60 and last["confidence"] == 0.8, last["confidence"]
    assert last["complete"] is True and last["current_depth"] == 100.0
    s = last["structure"]
    assert s["trend"] == "uptrend" and s["phase"] == "established"
    assert s["external_high"] == 12 and s["external_low"] == 8
    assert s["last_shift"]["type"] == "bos" and s["last_shift"]["direction"] == "up"
    print(f"OK — لقطة موحّدة: trend={s['trend']} phase={s['phase']} "
          f"ext=[{s['external_low']},{s['external_high']}] shift={s['last_shift']['type']}")


async def test_no_symbol_skips():
    print("\n--- test_no_symbol_skips ---")
    atom, bus = await _mk()
    v = _validated(_full_results())
    v.pop("symbol")
    await atom._on_validated(v)
    assert not _out(bus), "بلا symbol = لا نشر (713 يحتاج symbol)"
    print("OK — بلا symbol: لا نشر")


async def test_insufficient_when_no_valid():
    print("\n--- test_insufficient_when_no_valid ---")
    atom, bus = await _mk()
    results = {"structure_trend": _u("structure_trend", "range", status="insufficient_data")}
    await atom._on_validated(_validated(results))
    last = _out(bus)[-1]
    assert last["status"] == "insufficient_data", last["status"]
    print("OK — بلا وحدات صالحة → insufficient_data")


async def test_complete_with_required_only():
    print("\n--- test_complete_with_required_only ---")
    # (أ) دورة فيها 201/202/203/207 فقط (بلا 204/205/206/208 الاختياريّة)
    # يجب أن تُعطى complete=True (§٢٠: الاختياريّة لا تمنع الاكتمال).
    atom, bus = await _mk()
    results = {
        "swing": _u("swing", "swing_high", meta={"price": 12, "close": 13}),
        "external": _u("external", "HH",
                       meta={"swing_high": 12, "swing_low": 8, "close": 13}),
        "internal": _u("internal", "bullish"),
        "structure_trend": _u("structure_trend", "uptrend", score=60, confidence=1.0),
    }
    await atom._on_validated(_validated(results))
    last = _out(bus)[-1]
    assert last["complete"] is True, last
    assert last["status"] == "ok", last["status"]
    assert last["current_depth"] == 100.0, last["current_depth"]
    print("OK — الإلزاميّات الأربع فقط (بلا 204-208) → complete=True")


async def test_missing_required_blocks_completion():
    print("\n--- test_missing_required_blocks_completion ---")
    # ذرّة إلزاميّة غائبة (internal) رغم حضور كل الاختياريّة ⇒ لا اكتمال.
    atom, bus = await _mk()
    results = _full_results()
    results.pop("internal")
    await atom._on_validated(_validated(results))
    last = _out(bus)[-1]
    assert last["complete"] is False, last
    assert last["status"] == "insufficient_data", last["status"]
    assert last["current_depth"] == 75.0, last["current_depth"]
    print("OK — internal غائبة ⇒ complete=False رغم حضور الاختياريّة")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    atom, bus = await _mk()
    await atom._on_validated(_validated(_full_results()))
    last = _out(bus)[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "structure", "metadata"):
        assert field in last, f"حقل ناقص: {field}"
    for field in ("trend", "phase", "swing", "external_high", "external_low",
                  "internal", "last_shift"):
        assert field in last["structure"], f"حقل structure ناقص: {field}"
    assert last["id"] == "structure"
    print("OK — العقد + لقطة البنية كاملة")


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
    await atom._on_validated(_validated(_full_results()))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_assembles_snapshot,
        test_no_symbol_skips,
        test_insufficient_when_no_valid,
        test_complete_with_required_only,
        test_missing_required_blocks_completion,
        test_contract_shape_complete,
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
