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
    "_atom467", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom467"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_PASSED = _mod.EVENT_GATE_PASSED
EVENT_BLOCKED = _mod.EVENT_GATE_BLOCKED
EVENT_RECORDED = _mod.EVENT_GATE_RECORDED


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

    def events(self, name):
        return [p for n, p in self.published if n == name]

    def make_context(self, config):
        return AtomContext(atom_id=467, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


# الشكل التعاقدي الكامل لمخرج 466 (عقد الجيران — الهوية الست + الثمانية + الحواجز)
def _contract(side="buy", approved=True, **extra):
    body = {"symbol": "NQ100", "timeframe": "60s", "account_id": "A1",
            "broker": "BR", "cycle_id": "NQ100|60s|1000.0", "period_start": 1000.0,
            "decision_id": "dec-1", "decision_side": side, "approved": approved,
            "strength": 0.8, "score": 80,
            "barriers": [{"value": 0.83, "threshold": 0.8,
                          "reason": "consensus", "measured_at": 999.0}]}
    body.update(extra)
    return body


# شكل مخرج 466 القائم اليوم (نشر متدرج — بلا الهوية الست الكاملة)
def _legacy(approved, direction):
    return {"symbol": "NQ100", "timeframe": "60s", "cycle_id": "NQ100|60s|0.0",
            "signal": direction if approved else "none",
            "metadata": {"approved": approved, "direction": direction,
                         "request_id": "NQ100|60s|0.0"}}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    await atom._on_pulse({"official_time": 1234.5})
    return atom, bus


async def test_q9_1_approved_passes_with_full_identity():
    """قبول ق٩ (١): قرار معتمد → البوابة → حدث passed بهوية كاملة."""
    print("\n--- test_q9_1_approved_passes_with_full_identity ---")
    atom, bus = await _new()
    await atom._on_approved(_contract("buy", True))
    passed = bus.events(EVENT_PASSED)
    assert len(passed) == 1, passed
    p = passed[0]
    for field in ("account_id", "broker", "symbol", "timeframe", "cycle_id", "decision_id"):
        assert p[field] == _contract()[field], (field, p.get(field))
    assert p["decision_side"] == "buy" and p["gate_state"] == "PASSED"
    assert p["gate_request_id"] == "dec-1:req1", p["gate_request_id"]
    assert p["gated_at"] == 1234.5, p["gated_at"]
    assert p["gate_warnings"] == [], p["gate_warnings"]
    # pass-through: the eight and the barriers travel unchanged
    assert p["strength"] == 0.8 and p["score"] == 80
    assert p["barriers"] == _contract()["barriers"]
    # dispatch record for the decision store (707) is executable now
    outs = bus.events(EVENT_OUT)
    assert len(outs) == 1 and outs[0]["executable"] is True, outs
    assert outs[0]["request_id"] == "dec-1:req1" and outs[0]["decision_id"] == "dec-1"
    assert outs[0]["side"] == "BUY"
    assert not bus.events(EVENT_BLOCKED) and not bus.events(EVENT_RECORDED)
    print("OK — passed بهوية ست كاملة + gate_request_id حتمي + سجل إرسال executable")


async def test_q9_2_not_approved_never_passes():
    """قبول ق٩ (٢): غير معتمد → لا يمر ويُسجل blocked."""
    print("\n--- test_q9_2_not_approved_never_passes ---")
    atom, bus = await _new()
    await atom._on_approved(_contract("sell", False, reason="BLOCKED_UPSTREAM"))
    assert not bus.events(EVENT_PASSED) and not bus.events(EVENT_OUT)
    blocked = bus.events(EVENT_BLOCKED)
    assert len(blocked) == 1, blocked
    b = blocked[0]
    assert b["gate_state"] == "BLOCKED" and b["reject_reason"] == "BLOCKED_UPSTREAM"
    assert b["decision_side"] == "sell" and b["decision_id"] == "dec-1"
    assert b["gated_at"] == 1234.5
    assert b["barriers"] == _contract()["barriers"]
    print("OK — approved=false لا يمر أبدًا · blocked يحمل السبب والهوية كما وصلت")


async def test_q9_3_wait_recorded_not_an_order():
    """قبول ق٩ (٣): انتظار → recorded حالة قرار لا أمر."""
    print("\n--- test_q9_3_wait_recorded_not_an_order ---")
    atom, bus = await _new()
    await atom._on_approved(_contract("wait", True))
    assert not bus.events(EVENT_PASSED) and not bus.events(EVENT_OUT)
    recorded = bus.events(EVENT_RECORDED)
    assert len(recorded) == 1 and recorded[0]["gate_state"] == "RECORDED"
    assert recorded[0]["decision_side"] == "wait"
    print("OK — wait → recorded فقط، لا gate.passed ولا سجل إرسال")


async def test_duplicate_same_decision_blocked():
    """منع التكرار: نفس decision_id لا ينشر gate.passed مرتين."""
    print("\n--- test_duplicate_same_decision_blocked ---")
    atom, bus = await _new()
    await atom._on_approved(_contract("buy", True))
    await atom._on_approved(_contract("buy", True))
    assert len(bus.events(EVENT_PASSED)) == 1, "duplicate passed twice"
    h = await atom.health_check()
    assert h.details["duplicates"] == 1 and h.details["passed"] == 1, h.details
    print("OK — التكرار الصامت محجوب ومعلن بعداد الصحة")


async def test_documented_redispatch_raises_suffix():
    """التكرار الموثق بسبب تنفيذي يرفع اللاحقة req2."""
    print("\n--- test_documented_redispatch_raises_suffix ---")
    atom, bus = await _new()
    await atom._on_approved(_contract("buy", True))
    await atom._on_approved(_contract("buy", True, redispatch_reason="BRIDGE_TIMEOUT"))
    passed = bus.events(EVENT_PASSED)
    assert len(passed) == 2, len(passed)
    assert passed[1]["gate_request_id"] == "dec-1:req2", passed[1]["gate_request_id"]
    assert passed[1]["redispatch_reason"] == "BRIDGE_TIMEOUT"
    print("OK — سبب تنفيذي موثق → dec-1:req2")


async def test_legacy_payload_identity_incomplete_warning():
    """نشر متدرج: مخرج 466 القائم اليوم يمر مع إعلان identity_incomplete."""
    print("\n--- test_legacy_payload_identity_incomplete_warning ---")
    atom, bus = await _new()
    await atom._on_approved(_legacy(True, "buy"))
    passed = bus.events(EVENT_PASSED)
    assert len(passed) == 1, passed
    p = passed[0]
    assert p["gate_warnings"] == ["identity_incomplete"], p["gate_warnings"]
    assert "account_id" in p["identity_missing"] and "decision_id" in p["identity_missing"]
    # المعرف المتوفر فعلا هو cycle_id — لا اختراع لهوية غائبة
    assert p["gate_request_id"] == "NQ100|60s|0.0:req1", p["gate_request_id"]
    h = await atom.health_check()
    assert h.details["identity_incomplete"] == 1
    print("OK — الشكل القديم يعمل بالمتوفر مع إعلان النقص")


async def test_legacy_not_approved_still_blocked():
    print("\n--- test_legacy_not_approved_still_blocked ---")
    atom, bus = await _new()
    await atom._on_approved(_legacy(False, "buy"))
    assert not bus.events(EVENT_PASSED) and not bus.events(EVENT_OUT)
    assert len(bus.events(EVENT_BLOCKED)) == 1
    print("OK — قاعدة «لا approved=false يمر» صارمة على الشكل القديم أيضًا")


async def test_side_unknown_and_no_identity_blocked():
    print("\n--- test_side_unknown_and_no_identity_blocked ---")
    atom, bus = await _new()
    await atom._on_approved(_contract("sideways", True))
    assert bus.events(EVENT_BLOCKED)[-1]["reject_reason"] == "SIDE_UNKNOWN"
    await atom._on_approved(_contract("buy", True, decision_id="", cycle_id=""))
    assert bus.events(EVENT_BLOCKED)[-1]["reject_reason"] == "NO_DECISION_IDENTITY"
    assert not bus.events(EVENT_PASSED)
    print("OK — جانب مجهول أو هوية معدومة لا يمران")


async def test_dedup_memory_survives_restore():
    print("\n--- test_dedup_memory_survives_restore ---")
    atom, bus = await _new()
    await atom._on_approved(_contract("buy", True))
    saved = await atom.snapshot()
    fresh, bus2 = await _new()
    await fresh.restore(saved)
    await fresh._on_approved(_contract("buy", True))
    assert not bus2.events(EVENT_PASSED), "duplicate passed after restore"
    h = await fresh.health_check()
    assert h.details["duplicates"] == 1
    await fresh.restore({"attempts": "garbage"})
    assert (await fresh.health_check()).details.get("restore_note")
    print("OK — ذاكرة منع التكرار تنجو من الإقلاع، والفاسد يبدأ فارغًا معلنًا")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_approved(_contract("sell", True))
    h = await atom.health_check()
    assert h.state == HealthState.HEALTHY
    assert h.details["passed"] == 1 and h.details["blocked"] == 0
    assert h.details["recorded"] == 0 and h.details["duplicates"] == 0
    print("OK — الصحة: عدادات passed/blocked/recorded/duplicates")


async def main():
    tests = [test_q9_1_approved_passes_with_full_identity,
             test_q9_2_not_approved_never_passes,
             test_q9_3_wait_recorded_not_an_order,
             test_duplicate_same_decision_blocked,
             test_documented_redispatch_raises_suffix,
             test_legacy_payload_identity_incomplete_warning,
             test_legacy_not_approved_still_blocked,
             test_side_unknown_and_no_identity_blocked,
             test_dedup_memory_survives_restore,
             test_health]
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
