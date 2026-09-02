import asyncio
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom466", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom466"] = _mod
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
        return AtomContext(atom_id=466, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _filtered(direction, passed, score=80):
    return {"symbol": "NQ100", "timeframe": "60s", "cycle_id": "NQ100|60s|0.0",
            "signal": direction, "score": score, "metadata": {"passed": passed}}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    await atom.start()
    return atom, bus


def _last(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


async def test_approve_passed_buy():
    print("\n--- test_approve_passed_buy ---")
    atom, bus = await _new()
    await atom._on_filtered(_filtered("buy", True))
    last = _last(bus)
    assert last["metadata"]["approved"] is True
    assert last["metadata"]["request_id"] == "NQ100|60s|0.0"
    print("OK — buy مُمرَّر → approved")


async def test_reject_wait():
    print("\n--- test_reject_wait ---")
    atom, bus = await _new()
    await atom._on_filtered(_filtered("wait", False))
    assert _last(bus)["metadata"]["approved"] is False
    print("OK — wait → not approved")


async def test_reason_blocked_upstream():
    print("\n--- test_reason_blocked_upstream ---")
    atom, bus = await _new()
    # الفلتر ٤٥٤ سبق ورفضها (passed=False) — السبب هون لازم يميّز هالحالة
    # بالضبط، لا warnings فاضية زي ما كانت الذرّة تنشر قبل الإصلاح.
    await atom._on_filtered(_filtered("buy", False))
    last = _last(bus)
    assert last["metadata"]["approved"] is False
    assert last["metadata"]["reason"] == "BLOCKED_UPSTREAM", last["metadata"]["reason"]
    # حُدّث لتغيير الدلالة (بختم NQ بند 22 حزمة ب — ب١): حمولة الاختبار بلا
    # حساب/وسيط/بداية/معرّف، فيضاف إنذار identity_incomplete بجانب سبب الرفض.
    assert last["warnings"] == ["BLOCKED_UPSTREAM", "identity_incomplete"], last["warnings"]
    print("OK — مرفوض من ٤٥٤ → BLOCKED_UPSTREAM محدَّد")


async def test_reason_no_actionable_signal():
    print("\n--- test_reason_no_actionable_signal ---")
    atom, bus = await _new()
    # مرّ الفلتر (passed=True) لكن الاتجاه نفسه مو شراء ولا بيع — سبب مختلف
    # تمامًا عن الحجب بالفلتر، ولازم ما ينخلط فيه.
    await atom._on_filtered(_filtered("wait", True))
    last = _last(bus)
    assert last["metadata"]["approved"] is False
    assert last["metadata"]["reason"] == "NO_ACTIONABLE_SIGNAL", last["metadata"]["reason"]
    # حُدّث لتغيير الدلالة (ب١): إنذار الهوية الناقصة يضاف لسبب الرفض.
    assert last["warnings"] == ["NO_ACTIONABLE_SIGNAL", "identity_incomplete"], last["warnings"]
    print("OK — مرّ الفلتر بلا اتجاه فعليّ → NO_ACTIONABLE_SIGNAL محدَّد")


async def test_reason_none_when_approved():
    print("\n--- test_reason_none_when_approved ---")
    atom, bus = await _new()
    await atom._on_filtered(_filtered("buy", True))
    last = _last(bus)
    assert last["metadata"]["approved"] is True
    assert last["metadata"]["reason"] is None
    assert last["rejection"] is None, last["rejection"]
    # حُدّث لتغيير الدلالة (ب١): القبول لا يمسح إنذار الهوية الناقصة —
    # حمولة الاختبار بلا هوية كاملة فيبقى الإنذار وحده بلا سبب رفض.
    assert last["warnings"] == ["identity_incomplete"], last["warnings"]
    print("OK — مُعتمَد → لا سبب رفض، وإنذار الهوية وحده")


IDENTITY = {"account_id": "ACC1", "broker": "RTL", "period_start": 0.0,
            "decision_id": "dec:ACC1|RTL|NQ100|60s|0.0"}


def _filtered_full(direction, passed, score=80, barriers=None, decision_side=None):
    payload = _filtered(direction, passed, score)
    payload.update(IDENTITY)
    if decision_side is not None:
        payload["decision_side"] = decision_side
    if barriers is not None:
        payload["barriers"] = barriers
    return payload


async def test_rejected_state_keeps_six_fields():
    print("\n--- test_rejected_state_keeps_six_fields ---")
    # بختم NQ بند 22 حزمة ب (ب٧ — حكم ق٩ §٢٢): القرار المرفوض يحفظ سبب
    # الرفض والمرحلة والقيمة والعتبة والوقت وهوية القرار — من رباعية أول
    # حاجز أعلنه 454.
    atom, bus = await _new()
    barrier = {"name": "score_gate", "value": 42.0, "threshold": 60.0,
               "reason": "SCORE_BELOW_MIN", "measured_at": 1755640000.0}
    before = time.time()
    await atom._on_filtered(_filtered_full("buy", False, score=42,
                                           barriers=[barrier]))
    last = _last(bus)
    rejection = last["rejection"]
    assert rejection is not None
    assert set(rejection) == {"reason", "stage", "value", "threshold",
                              "time", "decision_id"}, rejection
    assert rejection["reason"] == "SCORE_BELOW_MIN", rejection
    assert rejection["stage"] == "454", rejection
    assert rejection["value"] == 42.0 and rejection["threshold"] == 60.0
    assert rejection["time"] >= before, rejection
    assert rejection["decision_id"] == IDENTITY["decision_id"], rejection
    assert last["barriers"] == [barrier], "قائمة الحواجز لم تمرَّر كما وصلت"
    print("OK — سجل الرفض بحقوله الستة وقائمة الحواجز ممرّرة كما وصلت")


async def test_rejected_at_466_names_its_own_stage():
    print("\n--- test_rejected_at_466_names_its_own_stage ---")
    # ق٩ §٢٢: حين يحجب 466 نفسه (جانب غير قابل للتنفيذ رغم مرور 454)
    # فالمرحلة 466 والقيمة هي الجانب نفسه.
    atom, bus = await _new()
    await atom._on_filtered(_filtered_full("wait", True, decision_side="wait"))
    last = _last(bus)
    rejection = last["rejection"]
    assert rejection["reason"] == "NO_ACTIONABLE_SIGNAL", rejection
    assert rejection["stage"] == "466", rejection
    assert rejection["value"] == "wait", rejection
    assert rejection["threshold"] is None, rejection
    assert rejection["decision_id"] == IDENTITY["decision_id"]
    print("OK — حجب 466 الذاتي يسمّي مرحلته وقيمته")


async def test_unknown_upstream_verdict_blocks_explicitly():
    print("\n--- test_unknown_upstream_verdict_blocks_explicitly ---")
    # ب٦: غياب حكم 454 (لا metadata.passed) مجهول لا حجب معلوم — يرفض بسبب
    # صريح UPSTREAM_RESULT_UNKNOWN لا بمساواة زائفة مع False.
    atom, bus = await _new()
    payload = _filtered_full("buy", True)
    payload["metadata"] = {}  # الحكم غائب لا سالب
    await atom._on_filtered(payload)
    last = _last(bus)
    assert last["metadata"]["approved"] is False
    assert last["metadata"]["reason"] == "UPSTREAM_RESULT_UNKNOWN", last["metadata"]["reason"]
    assert last["rejection"]["stage"] == "466"
    print("OK — الحكم الغائب رفض بسببه الصريح لا كأنه False")


async def test_identity_six_fields_pass_without_loss():
    print("\n--- test_identity_six_fields_pass_without_loss ---")
    # ب١ — حكم ق٩ §١٧: كان 466 يسقط الحساب والوسيط وبداية الدورة والمعرّف؛
    # الآن الهوية الست تمرّ كاملة حتى القرار المعتمد النهائي.
    atom, bus = await _new()
    await atom._on_filtered(_filtered_full("buy", True, decision_side="buy"))
    last = _last(bus)
    assert last["account_id"] == "ACC1" and last["broker"] == "RTL"
    assert last["symbol"] == "NQ100" and last["timeframe"] == "60s"
    assert last["period_start"] == 0.0
    assert last["decision_id"] == IDENTITY["decision_id"]
    assert last["approved"] is True and last["decision_side"] == "buy"
    assert "identity_incomplete" not in last["warnings"], last["warnings"]
    assert last["identity_missing"] == [], last["identity_missing"]
    print("OK — الهوية الست وصلت القرار المعتمد بلا فقد")


async def test_health_contract():
    print("\n--- test_health_contract ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    await atom._on_filtered(_filtered("sell", True))
    last = _last(bus)
    assert last["id"] == "decision_approval"
    assert last["metadata"]["approved"] is True
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — العقد + الصحة")


async def main():
    tests = [test_approve_passed_buy, test_reject_wait, test_health_contract,
             test_reason_blocked_upstream, test_reason_no_actionable_signal,
             test_reason_none_when_approved,
             test_rejected_state_keeps_six_fields,
             test_rejected_at_466_names_its_own_stage,
             test_unknown_upstream_verdict_blocks_explicitly,
             test_identity_six_fields_pass_without_loss]
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
