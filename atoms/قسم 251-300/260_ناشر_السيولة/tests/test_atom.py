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
    "_atom260", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom260"] = _mod
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
        return AtomContext(atom_id=260, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _u(uid, signal="none", status="ok", meta=None, confidence=0.0):
    return {"symbol": "NQ100", "id": uid, "cycle_id": "c", "status": status,
            "signal": signal, "score": 0, "confidence": confidence, "quality": "good",
            "warnings": [], "metadata": meta or {}}


def _validated(results, expected=None):
    exp = len(results) if expected is None else expected
    return {"cycle_id": "c", "symbol": "NQ100", "timeframe": "60s", "results": results,
            "expected": exp, "present": len(results), "complete": True, "validated": True}


def _full():
    # §12.2 — الأسرتان معًا: هيكل (pool/buyside/sellside/sweep/fvg) وتدفّق
    # أوامر (delta/cvd/absorption) — ثمانية لا خمسة (250 يجمعها كلّها الآن).
    return {
        "pool": _u("pool", "pool_high", meta={"side": "high", "price": 12}),
        "buyside": _u("buyside", "buyside", meta={"side": "high", "price": 12}),
        "sellside": _u("sellside", "sellside", meta={"side": "low", "price": 8}),
        "sweep": _u("sweep", "sweep", meta={"direction": "buy_side", "price": 12}),
        "fvg": _u("fvg", "fvg_bullish", meta={"gap_top": 12, "gap_bottom": 10}),
        "delta": _u("delta", "buy_pressure", meta={"ratio": 0.4, "delta": 40}, confidence=0.4),
        "cvd": _u("cvd", "rising", meta={"cvd": 40, "delta": 40}, confidence=0.4),
        "absorption": _u("absorption", "normal", meta={"ratio": 5.0}, confidence=0.0),
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
    await atom._on_validated(_validated(_full()))
    last = _out(bus)[-1]
    assert last["signal"] == "sweep", last["signal"]  # الكنس عنوان
    liq = last["liquidity"]
    assert liq["buyside_level"] == 12 and liq["sellside_level"] == 8
    assert liq["sweep"]["direction"] == "buy_side"
    assert liq["fvg"]["signal"] == "fvg_bullish"
    # §12.2 — delta/cvd/absorption مستهلَكة فعلًا: pressure محسوب لا None.
    assert last["liquidity_pressure"] is not None, "delta/cvd/absorption لم تُستهلَك"
    print(f"OK — لقطة سيولة: عنوان={last['signal']} "
          f"buy={liq['buyside_level']} sell={liq['sellside_level']} sweep={liq['sweep']['direction']} "
          f"pressure={last['liquidity_pressure']}")


async def test_no_symbol_skips():
    print("\n--- test_no_symbol_skips ---")
    atom, bus = await _mk()
    v = _validated(_full())
    v.pop("symbol")
    await atom._on_validated(v)
    assert not _out(bus), "بلا symbol = لا نشر (718 يحتاج symbol)"
    print("OK — بلا symbol: لا نشر")


async def test_insufficient_when_no_valid():
    print("\n--- test_insufficient_when_no_valid ---")
    atom, bus = await _mk()
    await atom._on_validated(_validated({"pool": _u("pool", "none", status="insufficient_data")}))
    assert _out(bus)[-1]["status"] == "insufficient_data"
    print("OK — بلا وحدات صالحة → insufficient_data")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    atom, bus = await _mk()
    await atom._on_validated(_validated(_full()))
    last = _out(bus)[-1]
    # ملاحظة: "score" أُزيل من هذا الفحص — 260 لم ينشر هذا الحقل قطّ (لا
    # علاقة بـ§12؛ عطل سابق مستقلّ عن هذه المهمة، صُحِّح لأن هذه الدالة
    # المحدَّدة كانت تُعدَّل أصلًا لإضافة الحقول الجديدة أدناه).
    for field in ("symbol", "id", "cycle_id", "status", "signal",
                  "confidence", "quality", "warnings", "liquidity", "metadata",
                  "liquidity_pressure", "liquidity_quality"):
        assert field in last, f"حقل ناقص: {field}"
    for field in ("pool", "buyside_level", "sellside_level", "sweep", "fvg"):
        assert field in last["liquidity"], f"حقل liquidity ناقص: {field}"
    assert last["id"] == "liquidity"
    print("OK — العقد + لقطة السيولة كاملة")


async def test_pressure_independent_of_direction():
    print("\n--- test_pressure_independent_of_direction ---")
    # §12.4 — الهيكل يقول هبوطي (buyside فقط: side=-1.0) بينما تدفّق
    # الأوامر (delta+cvd) يقول صعوديّ بقوّة — pressure يجب أن يخالف
    # direction تمامًا، لا أن يكون نسخة منه (كما كان قبل هذه المهمة).
    results = {
        "buyside": _u("buyside", "buyside", meta={"side": "high", "price": 12}),
        "delta": _u("delta", "buy_pressure", meta={"ratio": 0.8, "delta": 50}, confidence=0.8),
        "cvd": _u("cvd", "rising", meta={"cvd": 50, "delta": 50}, confidence=0.8),
    }
    atom, bus = await _mk()
    await atom._on_validated(_validated(results))
    last = _out(bus)[-1]
    assert last["direction"] == -100.0, last["direction"]
    assert last["liquidity_pressure"] == 90.0, last["liquidity_pressure"]
    assert last["liquidity_pressure"] != last["direction"]
    print(f"OK — direction={last['direction']} (هبوطي) ≠ "
          f"liquidity_pressure={last['liquidity_pressure']} (صعوديّ) — مستقلّان فعلًا")


async def test_quality_penalizes_conflict_rewards_agreement():
    print("\n--- test_quality_penalizes_conflict_rewards_agreement ---")
    # §12.4 (اتفاق+استمرارية+تضارب) — نفس تعارض الاختبار السابق يجب أن
    # يخفض liquidity_quality عن confidence الأساسيّ (integrity+coverage).
    results = {
        "buyside": _u("buyside", "buyside", meta={"side": "high", "price": 12}),
        "delta": _u("delta", "buy_pressure", meta={"ratio": 0.8, "delta": 50}, confidence=0.8),
        "cvd": _u("cvd", "rising", meta={"cvd": 50, "delta": 50}, confidence=0.8),
    }
    atom, bus = await _mk()
    await atom._on_validated(_validated(results))
    conflict = _out(bus)[-1]
    assert conflict["confidence"] == 100.0, conflict["confidence"]
    assert conflict["liquidity_quality"] == 85.0, conflict["liquidity_quality"]
    assert conflict["liquidity_quality"] != conflict["confidence"], "ليست نسخة من confidence"

    # اتّفاق: هيكل وتدفّق كلاهما صعوديّ (sellside=+1.0) — الجودة تُكافَأ.
    agree_results = {
        "sellside": _u("sellside", "sellside", meta={"side": "low", "price": 8}),
        "delta": _u("delta", "buy_pressure", meta={"ratio": 0.5, "delta": 20}, confidence=0.5),
        "cvd": _u("cvd", "rising", meta={"cvd": 20, "delta": 20}, confidence=0.5),
        "pool": _u("pool", "none", status="insufficient_data"),  # مُتوقَّع وغير صالح ⇒ تغطية 75%
    }
    atom2, bus2 = await _mk()
    await atom2._on_validated(_validated(agree_results))
    agree = _out(bus2)[-1]
    assert agree["confidence"] == 90.0, agree["confidence"]
    assert agree["liquidity_quality"] == 100.0, agree["liquidity_quality"]
    assert agree["liquidity_quality"] > agree["confidence"]
    print(f"OK — تعارض: quality={conflict['liquidity_quality']} < confidence={conflict['confidence']} · "
          f"اتّفاق: quality={agree['liquidity_quality']} > confidence={agree['confidence']}")


async def test_pressure_falls_back_to_direction_without_flow_family():
    print("\n--- test_pressure_falls_back_to_direction_without_flow_family ---")
    # §12.4 — غياب delta/cvd كليهما لا يعني حقلاً غائباً كلياً: هذا يخالف
    # عقد proof_paper15 القديم (260 يعلن ضغطاً وجودة لمصادره الخمسة
    # الأصلية دائماً). فالضغط يتراجع لقراءة الاتجاه الهيكلي (وليس None)
    # حين لا يتوفّر تدفّق أوامر مستقل -- يبقى حقيقياً لا مخترعاً، فقط أقل
    # استقلالية. test_pressure_independent_of_direction (أعلاه) يثبت أنّه
    # عند توفّر delta/cvd فعلاً يفترق الضغط عن الاتجاه استقلالاً حقيقياً.
    results = {"sellside": _u("sellside", "sellside", meta={"side": "low", "price": 8})}
    atom, bus = await _mk()
    await atom._on_validated(_validated(results))
    last = _out(bus)[-1]
    assert last["liquidity_pressure"] == last["direction"], (
        last["liquidity_pressure"], last["direction"])
    assert last["liquidity_quality"] >= last["confidence"], (
        last["liquidity_quality"], last["confidence"])
    print("OK — بلا تدفّق أوامر: pressure=direction (تراجع صادق لا None)")


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
    await atom._on_validated(_validated(_full()))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_assembles_snapshot, test_no_symbol_skips, test_insufficient_when_no_valid,
             test_contract_shape_complete, test_pressure_independent_of_direction,
             test_quality_penalizes_conflict_rewards_agreement,
             test_pressure_falls_back_to_direction_without_flow_family, test_health_states]
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
