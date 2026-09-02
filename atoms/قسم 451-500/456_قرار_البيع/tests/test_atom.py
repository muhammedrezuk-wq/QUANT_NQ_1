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
    "_atom456", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom456"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_DIALS_STATE = _mod.EVENT_DIALS_STATE

CFG = {"sell_min_direction": 50.0, "min_strength": 45.0,
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
        return AtomContext(atom_id=456, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _scored(direction=None, strength=None, confidence=None, depth=None,
            state=None, unknown=None, identity=IDENTITY, **extra):
    # حُدّث لتغيير الدلالة (v1.3.0 — بختم NQ بند 22 حزمة ب، توصيل الثمانية):
    # 456 صار يقرأ مفاتيح 453 v3.8.0 الفعلية — القيمة الموقعة direction_value
    # (الكلمة تبقى في direction)، والمقياسان ×100 strength_value/
    # confidence_value، والعمق current_depth من مجمع 451، والحالة المجمعة
    # aggregate_state (حكم المالك ٢٠٢٦-٠٨-٢٠ ب NQ) — فالمساعد يبني الحمولة
    # بهذه المفاتيح نفسها.
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


async def test_q8_17_sell_eligible():
    print("\n--- test_q8_17_sell_eligible ---")
    atom, bus = await _new()
    # ق٨ §١٧ حرفيًا: -68 / 64 / 75 (العمق غير مذكور بالورقة — قيمة مجتازة).
    await atom._on_scored(_scored(-68.0, 64.0, 75.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "eligible" and last["reason"] is None
    checks = _by_name(last["checks"])
    assert all(c["passed"] for c in last["checks"])
    # العيار موجب 50 ويُطبَّق على الجانب السالب: العتبة الفعلية -50.
    assert checks["direction"]["threshold"] == -50.0
    assert checks["direction"]["value"] == -68.0
    assert "signal" not in last and "score" not in last
    print("OK — §17: بيع مؤهل عند -68/64/75/READY")


async def test_q8_16_buy_cycle_not_eligible_for_sell():
    print("\n--- test_q8_16_buy_cycle_not_eligible_for_sell ---")
    atom, bus = await _new()
    # دورة §١٦ (+72) عند فاحص البيع → غير مؤهل والسبب الاتجاه.
    await atom._on_scored(_scored(72.0, 61.0, 78.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "not_eligible"
    assert last["reason"] == "DIRECTION_ABOVE_THRESHOLD"
    checks = _by_name(last["checks"])
    assert not checks["direction"]["passed"]
    assert checks["strength"]["passed"] and checks["confidence"]["passed"]
    print("OK — §16 عند 456: بيع غير مؤهل والسبب معلَن")


async def test_q8_18_wait_cycle_not_eligible():
    print("\n--- test_q8_18_wait_cycle_not_eligible ---")
    atom, bus = await _new()
    # ق٨ §١٨: +30 / 35 / 61 → البيع غير مؤهل أيضًا.
    await atom._on_scored(_scored(30.0, 35.0, 61.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "not_eligible"
    assert last["reason"] == "DIRECTION_ABOVE_THRESHOLD"
    print("OK — §18 عند 456: غير مؤهل")


async def test_unknown_field_blocks_with_declared_reason():
    print("\n--- test_unknown_field_blocks_with_declared_reason ---")
    atom, bus = await _new()
    # القوة غائبة: المجهول يحجب بسبب معلَن — لا صفر مزوّر.
    await atom._on_scored(_scored(-68.0, None, 75.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "not_eligible"
    assert last["reason"] == "FIELD_UNKNOWN:strength"
    check = _by_name(last["checks"])["strength"]
    assert check["value"] is None and check["passed"] is False
    print("OK — المجهول يحجب الأهلية بسبب معلَن")


async def test_parent_identity_passes():
    print("\n--- test_parent_identity_passes ---")
    atom, bus = await _new()
    await atom._on_scored(_scored(-68.0, 64.0, 75.0, 81.0, "READY"))
    last = _last(bus)
    for key, value in IDENTITY.items():
        assert last[key] == value, (key, last[key])
    assert last["warnings"] == [] and last["missing_identity"] == []
    print("OK — الهوية الست تمرّ بلا فقد:", last["decision_id"])


async def test_missing_decision_id_declared_not_invented():
    print("\n--- test_missing_decision_id_declared_not_invented ---")
    atom, bus = await _new()
    identity = {k: v for k, v in IDENTITY.items() if k != "decision_id"}
    await atom._on_scored(_scored(-68.0, 64.0, 75.0, 81.0, "READY",
                                  identity=identity))
    last = _last(bus)
    assert last["decision_id"] is None
    assert "identity_incomplete" in last["warnings"]
    assert "decision_id" in last["missing_identity"]
    print("OK — النشر المتدرج: decision_id الغائب يُعلَن ولا يُخترع")


async def test_q8_19_zero_dial_half_conflict_no_arbitration():
    print("\n--- test_q8_19_zero_dial_half_conflict_no_arbitration ---")
    cfg = dict(CFG, sell_min_direction=0.0)
    atom, bus = await _new(cfg)
    # نصف §١٩ عند 456: عتبة صفرية واتجاه صفر → مؤهل، بلا أي حسم هنا.
    await atom._on_scored(_scored(0.0, 61.0, 78.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "eligible" and last["reason"] is None
    assert "final_decision" not in last and "signal" not in last
    print("OK — §19 (نصف البيع): مؤهل ولا حسم — الحسم عند 458")


async def test_dial_command_applies_live():
    print("\n--- test_dial_command_applies_live ---")
    atom, bus = await _new()
    await atom._on_dial_command({"name": "DECISION_SELL_MIN_DIRECTION",
                                 "value": 70.0, "command_id": "CMD-1",
                                 "operator": "NQ", "approved_at": 123.0})
    states = [p for n, p in bus.published if n == EVENT_DIALS_STATE]
    assert states and states[-1]["dials"]["DECISION_SELL_MIN_DIRECTION"] == 70.0
    await atom._on_scored(_scored(-68.0, 64.0, 75.0, 81.0, "READY"))
    last = _last(bus)
    assert last["status"] == "not_eligible"
    assert last["reason"] == "DIRECTION_ABOVE_THRESHOLD"
    assert _by_name(last["checks"])["direction"]["threshold"] == -70.0
    print("OK — عيار المالك يطبَّق حيًّا على الجانب السالب")


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
    await atom._on_scored(_scored(-68.0, 64.0, 75.0, 81.0, "READY"))
    health = await atom.health_check()
    assert health.state == HealthState.HEALTHY and "eligible=1" in health.message
    print("OK — الصحة:", health.message)


async def main():
    tests = [test_q8_17_sell_eligible, test_q8_16_buy_cycle_not_eligible_for_sell,
             test_q8_18_wait_cycle_not_eligible,
             test_unknown_field_blocks_with_declared_reason,
             test_parent_identity_passes,
             test_missing_decision_id_declared_not_invented,
             test_q8_19_zero_dial_half_conflict_no_arbitration,
             test_dial_command_applies_live, test_health]
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
