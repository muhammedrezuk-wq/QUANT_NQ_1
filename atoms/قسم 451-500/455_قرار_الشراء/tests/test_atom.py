import asyncio
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# عزل سجلّ المُعامِلات: الاختبار لا يقرأ ولا يكتب القاعدة الحيّة أبدًا.
os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = os.path.join(
    tempfile.mkdtemp(), "analysis_settings_test.db")

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom455", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom455"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_DIALS_STATE = _mod.EVENT_DIALS_STATE

CFG = {"buy_min_direction": 50.0, "min_strength": 45.0,
       "min_confidence": 63.0, "min_current_depth": 45.0}

IDENTITY = {"account_id": "ACC-1", "broker": "ctrader", "symbol": "BTCUSD",
            "timeframe": "60s", "period_start": 7.0, "decision_id": "D-1"}


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
        return AtomContext(atom_id=455, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _scored(direction=None, strength=None, confidence=None, depth=None,
            state=None, unknown=None, identity=IDENTITY, **extra):
    # حُدّث لتغيير الدلالة (v1.3.0 — بختم NQ بند 22 حزمة ب، توصيل الثمانية):
    # 455 صار يقرأ مفاتيح 453 v3.8.0 الفعلية — القيمة الموقعة direction_value
    # (الكلمة تبقى في direction)، والمقياسان ×100 strength_value/
    # confidence_value، والعمق current_depth من مجمع 451، والحالة المجمعة
    # aggregate_state (حكم المالك ٢٠٢٦-٠٨-٢٠ ب NQ) — فالمساعد يبني الحمولة
    # بهذه المفاتيح نفسها. القياس الحي قبل التوصيل: 276 دورة كلها
    # FIELD_UNKNOWN:direction لأن الكلمة كانت تُقرأ رقمًا.
    payload = {"cycle_id": "BTCUSD|60s|7.0", "source_timestamp": 7.5}
    payload.update(identity)
    if direction is not None: payload["direction_value"] = direction
    if strength is not None: payload["strength_value"] = strength
    if confidence is not None: payload["confidence_value"] = confidence
    if depth is not None: payload["current_depth"] = depth
    if state is not None: payload["aggregate_state"] = state
    if unknown is not None: payload["unknown_fields"] = unknown
    payload.update(extra)
    return payload


async def _new(config=None):
    os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = os.path.join(
        tempfile.mkdtemp(), "analysis_settings_test.db")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(config or CFG)))
    await atom.start()
    return atom, bus


def _last(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


def _by_name(checks):
    return {c["name"]: c for c in checks}


async def test_q8_16_buy_eligible():
    print("\n--- test_q8_16_buy_eligible ---")
    atom, bus = await _new()
    # ق٨ §١٦ حرفيًا: +72 / 61 / 78 / 81 والحالة READY → شراء مؤهل.
    await atom._on_scored(_scored(72.0, 61.0, 78.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "eligible" and last["reason"] is None
    checks = _by_name(last["checks"])
    assert set(checks) == {"direction", "strength", "confidence", "current_depth", "state"}
    assert all(c["passed"] for c in last["checks"])
    assert checks["direction"]["threshold"] == 50.0
    assert checks["strength"]["threshold"] == 45.0
    assert checks["confidence"]["threshold"] == 63.0
    assert checks["current_depth"]["threshold"] == 45.0
    assert checks["state"]["threshold"] == "READY"
    # لا قرار ولا أمر — حالة أهلية فقط.
    assert "signal" not in last and "score" not in last
    print("OK — §16: شراء مؤهل عند +72/61/78/81/READY")


async def test_q8_18_wait_cycle_not_eligible():
    print("\n--- test_q8_18_wait_cycle_not_eligible ---")
    atom, bus = await _new()
    # ق٨ §١٨ حرفيًا: +30 / 35 / 61 (العمق غير مذكور بالورقة — قيمة مجتازة).
    await atom._on_scored(_scored(30.0, 35.0, 61.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "not_eligible"
    assert last["reason"] == "DIRECTION_BELOW_THRESHOLD"
    checks = _by_name(last["checks"])
    assert not checks["direction"]["passed"] and checks["direction"]["value"] == 30.0
    assert not checks["strength"]["passed"] and not checks["confidence"]["passed"]
    assert checks["current_depth"]["passed"] and checks["state"]["passed"]
    print("OK — §18: غير مؤهل والفحوص تكشف الشروط الساقطة الثلاثة")


async def test_unknown_field_blocks_with_declared_reason():
    print("\n--- test_unknown_field_blocks_with_declared_reason ---")
    atom, bus = await _new()
    # الثقة غائبة كليًا: المجهول يحجب بسبب معلَن — لا يُقرأ صفرًا.
    await atom._on_scored(_scored(72.0, 61.0, None, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "not_eligible"
    assert last["reason"] == "FIELD_UNKNOWN:confidence"
    check = _by_name(last["checks"])["confidence"]
    assert check["value"] is None and check["passed"] is False
    assert check["threshold"] == 63.0
    # ومُعلَنة ضمن unknown_fields رغم وجود رقم — الإعلان يعلو القيمة.
    await atom._on_scored(_scored(72.0, 61.0, 78.0, 81.0, "READY",
                                  unknown=["confidence"]))
    last = _last(bus)
    assert last["reason"] == "FIELD_UNKNOWN:confidence"
    print("OK — المجهول يحجب الأهلية بسبب معلَن لا بصفر مزوّر")


async def test_parent_identity_passes():
    print("\n--- test_parent_identity_passes ---")
    atom, bus = await _new()
    await atom._on_scored(_scored(72.0, 61.0, 78.0, 81.0, "READY"))
    last = _last(bus)
    for key, value in IDENTITY.items():
        assert last[key] == value, (key, last[key])
    assert last["warnings"] == [] and last["missing_identity"] == []
    assert last["source_timestamp"] == 7.5
    print("OK — الهوية الست تمرّ بلا فقد:", last["decision_id"])


async def test_missing_decision_id_declared_not_invented():
    print("\n--- test_missing_decision_id_declared_not_invented ---")
    atom, bus = await _new()
    identity = {k: v for k, v in IDENTITY.items() if k != "decision_id"}
    await atom._on_scored(_scored(72.0, 61.0, 78.0, 81.0, "READY",
                                  identity=identity))
    last = _last(bus)
    assert last["decision_id"] is None
    assert "identity_incomplete" in last["warnings"]
    assert "decision_id" in last["missing_identity"]
    print("OK — النشر المتدرج: decision_id الغائب يُعلَن ولا يُخترع")


async def test_q8_19_zero_dial_half_conflict_no_arbitration():
    print("\n--- test_q8_19_zero_dial_half_conflict_no_arbitration ---")
    cfg = dict(CFG, buy_min_direction=0.0)
    atom, bus = await _new(cfg)
    # نصف §١٩ عند 455: عتبة صفرية واتجاه صفر → مؤهل، بلا أي حسم هنا.
    await atom._on_scored(_scored(0.0, 61.0, 78.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "eligible" and last["reason"] is None
    assert "final_decision" not in last and "signal" not in last
    print("OK — §19 (نصف الشراء): مؤهل ولا حسم — الحسم عند 458")


async def test_dial_command_applies_live():
    print("\n--- test_dial_command_applies_live ---")
    atom, bus = await _new()
    await atom._on_dial_command({"name": "DECISION_BUY_MIN_DIRECTION",
                                 "value": 60.0, "command_id": "CMD-1",
                                 "operator": "NQ", "approved_at": 123.0})
    states = [p for n, p in bus.published if n == EVENT_DIALS_STATE]
    assert states and states[-1]["dials"]["DECISION_BUY_MIN_DIRECTION"] == 60.0
    await atom._on_scored(_scored(55.0, 61.0, 78.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "not_eligible"
    assert last["reason"] == "DIRECTION_BELOW_THRESHOLD"
    assert _by_name(last["checks"])["direction"]["threshold"] == 60.0
    print("OK — عيار المالك يطبَّق حيًّا ويظهر بالعتبة المنشورة")


async def test_health():
    print("\n--- test_health ---")
    os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = os.path.join(
        tempfile.mkdtemp(), "analysis_settings_test.db")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_scored(_scored(72.0, 61.0, 78.0, 81.0, "READY"))
    health = await atom.health_check()
    assert health.state == HealthState.HEALTHY and "eligible=1" in health.message
    print("OK — الصحة:", health.message)


async def test_scored_v380_payload_becomes_eligible():
    print("\n--- test_scored_v380_payload_becomes_eligible ---")
    # (ج) توصيل الثمانية + حكم المالك ٢٠٢٦-٠٨-٢٠ (ب NQ): حمولة 453 v3.8.0
    # كما تُنشر فعلًا — الكلمة في direction والأرقام في المفاتيح الجديدة.
    # مثال ق٨ §١٦ (+72/61/78/81): مع aggregate_state=READY يصير 455 مؤهلًا
    # فعليًا؛ وبلا حالة مجمعة يسقط FIELD_UNKNOWN:state فقط (الغائب لا يُقرأ).
    atom, bus = await _new()
    realistic = {"cycle_id": "BTCUSD|60s|7.0", "source_timestamp": 7.5,
                 "direction": "buy", "signal": "buy", "score": 72.0,
                 "confidence": 0.78, "strength": 0.61,
                 "direction_value": 72.0, "strength_value": 61.0,
                 "confidence_value": 78.0, "current_depth": 81.0}
    realistic.update(IDENTITY)
    await atom._on_scored(dict(realistic))
    last = _last(bus)
    assert last["status"] == "not_eligible"
    assert last["reason"] == "FIELD_UNKNOWN:state", last["reason"]
    checks = _by_name(last["checks"])
    assert checks["direction"]["passed"] and checks["direction"]["value"] == 72.0
    assert checks["strength"]["passed"] and checks["strength"]["value"] == 61.0
    assert checks["confidence"]["passed"] and checks["confidence"]["value"] == 78.0
    assert checks["current_depth"]["passed"] and checks["current_depth"]["value"] == 81.0
    await atom._on_scored(dict(realistic, aggregate_state="READY",
                               state_missing_sections=[]))
    last = _last(bus)
    assert last["status"] == "eligible" and last["reason"] is None, last["reason"]
    assert all(c["passed"] for c in last["checks"])
    print("OK — §16 مؤهل فعليًا بالحقول الجديدة وREADY، وبلا حالة يبقى الغائب معلنًا")


async def main():
    tests = [test_q8_16_buy_eligible, test_q8_18_wait_cycle_not_eligible,
             test_unknown_field_blocks_with_declared_reason,
             test_parent_identity_passes,
             test_missing_decision_id_declared_not_invented,
             test_q8_19_zero_dial_half_conflict_no_arbitration,
             test_dial_command_applies_live,
             test_scored_v380_payload_becomes_eligible, test_health]
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
