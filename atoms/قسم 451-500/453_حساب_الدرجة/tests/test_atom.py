import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ⛔ عزل قاعدة المعايرة (٢٠٢٦-٠٨-٢١): كانت الاختبارات تقرأ
# `var/store/analysis_settings.db` الحيّة، فتصير نتيجتها تابعةً لمعايرة المالك
# لحظةَ التشغيل: أوزان التوليفة المعتمدة (وفيها أصفار مقصودة) أسقطت ثلاثة
# اختبارات كانت تفترض الحصص المتساوية. الاختبار يقيس الكود لا حالة الجهاز.
os.environ.setdefault(
    "QUANT_ANALYSIS_SETTINGS_DB",
    os.path.join(os.environ.get("TEMP", "."), "quant_nq_test_analysis_settings.db"))

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom453", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom453"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

# Owner's TEST weight contract 2026-08-13: weight belongs to the SOURCE, and a
# declared directional source keeps its full weight even while silent.
# Owner's ruling on problem 51 (option A -- the ROOTS): 404/406/407/408/410
# -> 413 -> 401, so 401/413 are derivatives; and 166 fuses 151+152 which are
# the very parents of roots 404/408, so it is contextual too (problem 33).
DIRECTIONAL = ["400:trend_strategy", "400:breakout_strategy",
               "400:pullback_strategy", "400:momentum_strategy",
               "400:liquidity_strategy"]
CFG = {"directional_weight": 1.0, "context_weight": 0.0556,
       "min_participation": 0.20, "directional_sources": list(DIRECTIONAL)}


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
        return AtomContext(atom_id=453, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _ev(source, direction, score, confidence, eligible=True, quality_factor=1.0,
        reason=""):
    return {"source": source, "label": source, "kind": "directional",
            "direction": direction, "score": score, "confidence": confidence,
            "quality_factor": quality_factor, "eligible": eligible, "fresh": True,
            "eligibility_reason": reason}


def _payload(rows):
    return {"symbol": "BTCUSD", "timeframe": "60s", "cycle_id": "BTCUSD|60s|0.0",
            "evidence": rows, "complete": True, "cycle_status": "complete", "status": "ok"}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


def _last(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


async def test_weighted_not_counted():
    print("\n--- test_weighted_not_counted ---")
    atom, bus = await _new()
    # Two weak strategy voices must not beat one strong, sure analysis voice.
    await atom._on_evaluated(_payload([
        _ev("400:trend_strategy", "sell", 100, 1.0),
        _ev("400:momentum_strategy", "buy", 100, 0.2),
        _ev("400:liquidity_strategy", "buy", 40, 0.5),
    ]))
    last = _last(bus)
    assert last["direction"] == "sell", last["direction"]
    assert last["sell_total"] > last["buy_total"]
    print("OK — الوزن والثقة يحكمان لا عدد الأصوات:", last["direction"],
          "buy=%.3f sell=%.3f" % (last["buy_total"], last["sell_total"]))


async def test_strength_is_real_number():
    print("\n--- test_strength_is_real_number ---")
    atom, bus = await _new()
    await atom._on_evaluated(_payload([
        _ev("400:trend_strategy", "buy", 50, 0.5),
        _ev("400:breakout_strategy", "sell", 50, 0.5),
        _ev("400:momentum_strategy", "buy", 60, 0.6),
    ]))
    last = _last(bus)
    assert 0.0 < last["strength"] < 1.0, last["strength"]
    assert last["strength"] not in (0.5, 1.0)
    expected = abs(last["net"]) / last["weight_present"]
    assert abs(last["strength"] - expected) < 1e-6, (last["strength"], expected)
    assert last["confidence"] == last["participation"]
    print("OK — strength=%.4f score=%.2f participation=%.4f" % (
        last["strength"], last["score"], last["participation"]))


async def test_full_agreement_reaches_one():
    print("\n--- test_full_agreement_reaches_one ---")
    atom, bus = await _new()
    await atom._on_evaluated(_payload([
        _ev("400:trend_strategy", "buy", 100, 1.0),
        _ev("400:momentum_strategy", "buy", 100, 1.0),
    ]))
    last = _last(bus)
    assert last["strength"] == 1.0, last["strength"]
    assert last["score"] == 100.0, last["score"]
    print("OK — اتفاق تامّ → strength=1.0")


async def test_opposition_cancels():
    print("\n--- test_opposition_cancels ---")
    atom, bus = await _new()
    await atom._on_evaluated(_payload([
        _ev("400:trend_strategy", "buy", 100, 1.0),
        _ev("400:breakout_strategy", "sell", 100, 1.0),
    ]))
    last = _last(bus)
    assert last["net"] == 0.0 and last["strength"] == 0.0, last
    assert last["direction"] == "neutral"
    print("OK — تعارض متكافئ → صافي صفر وقوّة صفر")


async def test_ineligible_ignored_and_health():
    print("\n--- test_ineligible_ignored_and_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    await atom._on_evaluated(_payload([
        _ev("400:trend_strategy", "buy", 100, 1.0),
        _ev("350:reversal_model", "buy", 80, 1.0, eligible=False,
            reason="NO_DIRECTION"),
        _ev("350:range_model", "buy", 80, 1.0, eligible=False,
            reason="STALE_CYCLE"),
    ]))
    last = _last(bus)
    assert len(last["contributions"]) == 1, last["contributions"]
    # Present-but-neutral stays in the denominator (1.0 + 0.0556); the STALE
    # source is genuinely absent and does not (availability != eligibility).
    present = 1.0 + 0.0556
    assert abs(last["weight_present"] - present) < 1e-9, last["weight_present"]
    assert abs(last["strength"] - (1.0 / present)) < 1e-6, last["strength"]
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — غير المؤهّل الحاضر يبقى بالمقام، والبائت يسقط + الصحة")


async def test_lone_voice_is_not_consensus():
    """حكم المالك 2026-08-13: متكلّم واحد بين حاضرين كثر ≠ إجماع — S صغيرة
    والمشاركة تحت العتبة تفرض WAIT."""
    print("\n--- test_lone_voice_is_not_consensus ---")
    atom, bus = await _new()
    rows = [_ev("400:liquidity_strategy", "sell", 100, 1.0)]
    # the other declared directional roots are present but silent — they
    # keep their full weight, which is exactly what stops one voice from
    # passing for a consensus.
    for name in DIRECTIONAL[:-1]:
        rows.append(_ev(name, "", 0, 0.0, eligible=False, reason="NO_DIRECTION"))
    for i in range(9):
        rows.append(_ev("350:model_%d" % i, "", 0, 0.0, eligible=False,
                        reason="NO_DIRECTION"))
    await atom._on_evaluated(_payload(rows))
    last = _last(bus)
    # present = len(DIRECTIONAL)×1.0 + 9×0.0556 · spoken 1.0 ⇒ دون 0.20
    present = len(DIRECTIONAL) * 1.0 + 9 * 0.0556
    assert abs(last["participation"] - 1.0 / present) < 1e-6, last["participation"]
    assert abs(last["strength"] - 1.0 / present) < 1e-6, last["strength"]
    assert last["direction"] == "neutral", "مشاركة تحت العتبة → WAIT"
    assert "LOW_PARTICIPATION" in last["warnings"]
    print("OK — صوت وحيد: S=%.2f مشاركة=%.2f → WAIT" % (
        last["strength"], last["participation"]))


async def test_participation_above_threshold_passes():
    print("\n--- test_participation_above_threshold_passes ---")
    atom, bus = await _new()
    rows = [_ev("400:trend_strategy", "buy", 80, 1.0), _ev("400:momentum_strategy", "buy", 60, 0.8)]
    for i in range(3):
        rows.append(_ev("350:model_%d" % i, "", 0, 0.0, eligible=False,
                        reason="NO_DIRECTION"))
    await atom._on_evaluated(_payload(rows))
    last = _last(bus)
    # spoken 2.0 / present 3.5 = 0.571 ≥ 0.20 → الاتجاه يمرّ
    assert last["participation"] > 0.5, last["participation"]
    assert last["direction"] == "buy", last["direction"]
    assert "LOW_PARTICIPATION" not in last["warnings"]
    print("OK — مشاركة %.2f فوق العتبة → الاتجاه يمرّ" % last["participation"])


async def test_identity_six_fields_pass_without_loss():
    print("\n--- test_identity_six_fields_pass_without_loss ---")
    # بختم NQ بند 22 حزمة ب (ب١ — حكم ق٩ §١٧): الهوية الست تقرأ من المدخل
    # وتعاد بالمخرج كاملة عبر 453 (الوسيط والمعرّف كانا لا يمران).
    atom, bus = await _new()
    payload = _payload([_ev("400:trend_strategy", "buy", 100, 1.0)])
    payload.update({"account_id": "ACC1", "broker": "Raw Trading Ltd",
                    "period_start": 0.0, "decision_id": "dec:ACC1|RTL|BTCUSD|60s|0.0"})
    await atom._on_evaluated(payload)
    last = _last(bus)
    assert last["account_id"] == "ACC1" and last["broker"] == "Raw Trading Ltd"
    assert last["symbol"] == "BTCUSD" and last["timeframe"] == "60s"
    assert last["period_start"] == 0.0
    assert last["decision_id"] == "dec:ACC1|RTL|BTCUSD|60s|0.0"
    assert "identity_incomplete" not in last["warnings"], last["warnings"]
    assert last["identity_missing"] == [], last["identity_missing"]
    print("OK — الهوية الست مرّت بلا فقد ولا إنذار")


async def test_incomplete_identity_declared_not_invented():
    print("\n--- test_incomplete_identity_declared_not_invented ---")
    # ب١: الناقص يعاد None ويعلن identity_incomplete بأسماء الغائب — لا اختراع.
    # ملاحظة: period_start=0.0 هنا قيمة حاضرة (ليست غيابًا) — الغائب حقًا هو
    # الحساب والوسيط والمعرّف.
    atom, bus = await _new()
    payload = _payload([_ev("400:trend_strategy", "buy", 100, 1.0)])
    payload["period_start"] = 0.0
    await atom._on_evaluated(payload)
    last = _last(bus)
    assert last["account_id"] is None and last["broker"] is None
    assert last["decision_id"] is None
    assert last["period_start"] == 0.0, last["period_start"]
    assert "identity_incomplete" in last["warnings"], last["warnings"]
    assert last["identity_missing"] == ["account_id", "broker", "decision_id"], \
        last["identity_missing"]
    print("OK — الغائب None ومعلَن بالاسم، والحاضر (0.0) لم يُحسب غائبًا")


async def test_direction_value_signed_and_scales_x100():
    print("\n--- test_direction_value_signed_and_scales_x100 ---")
    # بختم NQ بند 22 حزمة ب — توصيل الثمانية: direction_value ترميز العقد
    # المنشور نفسه (الكلمة إشارة والدرجة مقدار) رقمًا موقعًا واحدًا: +score
    # شراء، -score بيع، 0.0 حياد — والكلمة النهائية تحكم الإشارة (تحييد
    # LOW_PARTICIPATION يصفّر القيمة الموقعة أيضًا). وstrength_value/
    # confidence_value تحويل مقياس ×100 للقيم المقيسة نفسها — لا اختراع.
    atom, bus = await _new()
    await atom._on_evaluated(_payload([
        _ev("400:trend_strategy", "buy", 80, 1.0),
        _ev("400:momentum_strategy", "buy", 60, 0.8)]))
    last = _last(bus)
    assert last["direction"] == "buy", last["direction"]
    assert last["direction_value"] == last["score"] > 0, last["direction_value"]
    assert abs(last["strength_value"] - last["strength"] * 100.0) < 1e-6
    assert abs(last["confidence_value"] - last["confidence"] * 100.0) < 1e-6
    await atom._on_evaluated(_payload([
        _ev("400:trend_strategy", "sell", 100, 1.0),
        _ev("400:momentum_strategy", "sell", 60, 0.8)]))
    last = _last(bus)
    assert last["direction"] == "sell", last["direction"]
    assert last["direction_value"] == -last["score"] < 0, last["direction_value"]
    await atom._on_evaluated(_payload([
        _ev("400:trend_strategy", "buy", 100, 1.0),
        _ev("400:breakout_strategy", "sell", 100, 1.0)]))
    last = _last(bus)
    assert last["direction"] == "neutral" and last["direction_value"] == 0.0
    # تحييد المشاركة المنخفضة: score يبقى مقدارًا لكن الكلمة صارت neutral —
    # فالقيمة الموقعة صفر (الكلمة هي الإشارة).
    rows = [_ev("400:liquidity_strategy", "sell", 100, 1.0)]
    for name in DIRECTIONAL[:-1]:
        rows.append(_ev(name, "", 0, 0.0, eligible=False, reason="NO_DIRECTION"))
    for i in range(9):
        rows.append(_ev("350:model_%d" % i, "", 0, 0.0, eligible=False,
                        reason="NO_DIRECTION"))
    await atom._on_evaluated(_payload(rows))
    last = _last(bus)
    assert last["direction"] == "neutral" and last["score"] > 0
    assert last["direction_value"] == 0.0, last["direction_value"]
    print("OK — القيمة الموقعة تتبع الكلمة، والمقاييس ×100 صادقة")


async def test_aggregate_fields_pass_only_when_present():
    print("\n--- test_aggregate_fields_pass_only_when_present ---")
    # توصيل الثمانية + حكم المالك ٢٠٢٦-٠٨-٢٠ (ب NQ): العمق المجمع وحالة
    # الأقسام المجمعة يمران من مدخل 452 كما هما إن وُجدا — والغائب لا
    # يُخترع (لا مفاتيح من عدم).
    atom, bus = await _new()
    payload = _payload([_ev("400:trend_strategy", "buy", 100, 1.0)])
    payload.update({"current_depth": 83.5, "required_depth": None,
                    "depth_unknown_fields": ["required_depth"],
                    "aggregate_state": "READY", "state_missing_sections": []})
    await atom._on_evaluated(payload)
    last = _last(bus)
    assert last["current_depth"] == 83.5, last["current_depth"]
    assert last["required_depth"] is None
    assert last["depth_unknown_fields"] == ["required_depth"]
    assert last["aggregate_state"] == "READY"
    assert last["state_missing_sections"] == []
    await atom._on_evaluated(_payload([_ev("400:trend_strategy", "buy", 100, 1.0)]))
    bare = _last(bus)
    for key in ("current_depth", "required_depth", "depth_unknown_fields",
                "aggregate_state", "state_missing_sections"):
        assert key not in bare, key
    print("OK — الحاضر يمرّ كما هو والغائب لا يُخترع")


async def main():
    tests = [test_weighted_not_counted, test_strength_is_real_number,
             test_full_agreement_reaches_one, test_opposition_cancels,
             test_ineligible_ignored_and_health,
             test_lone_voice_is_not_consensus,
             test_participation_above_threshold_passes,
             test_identity_six_fields_pass_without_loss,
             test_incomplete_identity_declared_not_invented,
             test_direction_value_signed_and_scales_x100,
             test_aggregate_fields_pass_only_when_present]
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
