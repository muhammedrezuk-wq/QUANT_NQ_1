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
    "_atom458", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom458"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"neutral_band": 0.05, "conflict_ratio": 0.5}
CYCLE = "BTCUSD|60s|7.0"
# بختم NQ بند 22 حزمة ب (ب١): معرّف القرار يولد عند 451 ("dec:" + cycle_key)
# ويمرّ عبر السلسلة — 458 لم يعد يخترعه من cycle_id.
DECISION = "dec:ACC1|RTL|BTCUSD|60s|7.0"
IDENTITY = {"account_id": "ACC1", "broker": "RTL", "period_start": 7.0,
            "decision_id": DECISION}
# v3.1.0 (الحسم المقاد بالأحكام): دورة أحدث لنفس النطاق — تُستعمل لدفق
# الأقدم غير المحسوم (نمط 150 المختوم: الخلف هو المهلة، لا مؤقت).
NEWER_DECISION = "dec:ACC1|RTL|BTCUSD|60s|67.0"
NEWER_IDENTITY = {"account_id": "ACC1", "broker": "RTL", "period_start": 67.0,
                  "decision_id": NEWER_DECISION}


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
        return AtomContext(atom_id=458, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _scored(buy, sell, available, score=0.0, confidence=0.0, strength=0.0,
            identity=None):
    payload = {"symbol": "BTCUSD", "timeframe": "60s", "cycle_id": CYCLE,
               "buy_total": buy, "sell_total": sell, "weight_available": available,
               "weight_spoken": buy + sell, "score": score, "confidence": confidence,
               "strength": strength,
               "contributions": [{"source": "401", "direction": "buy", "contribution": buy}],
               "evidence": [{"source": "401", "eligible": True}]}
    if identity:
        payload.update(identity)
    return payload


def _eligibility(status, reason=None, decision_id=DECISION):
    # حمولة عقد الأهلية المتفق (ب٣): الهوية الست + decision_id + status
    # من {"eligible","not_eligible","inactive"} + reason + checks.
    return {"account_id": "ACC1", "broker": "RTL", "symbol": "BTCUSD",
            "timeframe": "60s", "period_start": 7.0, "decision_id": decision_id,
            "status": status, "reason": reason,
            "checks": [{"name": "confidence", "value": 0.8, "threshold": 0.5,
                        "passed": status == "eligible"}]}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


def _last(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


async def test_no_evidence_is_wait():
    print("\n--- test_no_evidence_is_wait ---")
    atom, bus = await _new()
    await atom._on_scored(_scored(0.0, 0.0, 0.0))
    last = _last(bus)
    assert last["direction"] == "wait" and last["reason"] == "NO_ELIGIBLE_EVIDENCE"
    assert last["strength"] == 0.0 and last["score"] == 0.0
    print("OK — بلا أدلّة → انتظار لا اتّجاه قديم")


async def test_balanced_is_neutral():
    print("\n--- test_balanced_is_neutral ---")
    atom, bus = await _new()
    await atom._on_scored(_scored(0.50, 0.49, 2.0, score=1.0, confidence=0.5, strength=0.005))
    last = _last(bus)
    assert last["direction"] == "neutral" and last["reason"] == "BALANCED_NO_EDGE"
    assert last["strength"] == 0.0
    print("OK — تعادل ضمن النطاق الميت → محايد")


async def test_conflict_is_flagged():
    print("\n--- test_conflict_is_flagged ---")
    atom, bus = await _new()
    await atom._on_scored(_scored(1.0, 0.6, 2.0, score=25.0, confidence=0.8, strength=0.2))
    last = _last(bus)
    assert last["direction"] == "buy", last["direction"]
    assert last["conflict"] is True and last["reason"] == "RESOLVED_WITH_CONFLICT"
    assert last["strength"] == 0.2
    print("OK — اتّجاه محسوم مع تسجيل التعارض")


async def test_clean_resolution_and_record():
    print("\n--- test_clean_resolution_and_record ---")
    # حُدّث لتغيير الدلالة (بختم NQ بند 22 حزمة ب — ب١): كان 458 يخترع
    # decision_id = cycle_id محليًّا؛ الآن المعرّف يولد عند 451 ويمرّ كما هو،
    # والغائب يبقى None مع إنذار identity_incomplete — لا اختراع.
    # وحُدّث ثانية (v3.1.0 — الحسم المقاد بالأحكام): دورة بمعرّف قرار لا
    # تحسم فور scored بل تنتظر ثلاثي الأهلية؛ وصول scored الدورة الأحدث
    # لنفس النطاق يدفقها بمنطق fallback القائم نفسه (نمط 150 المختوم).
    atom, bus = await _new()
    await atom._on_scored(_scored(0.0, 1.0, 1.0, score=100.0, confidence=1.0,
                                  strength=1.0, identity=IDENTITY))
    await atom._on_scored(_scored(0.0, 1.0, 1.0, score=100.0, confidence=1.0,
                                  strength=1.0, identity=NEWER_IDENTITY))
    last = _last(bus)
    assert last["direction"] == "sell" and last["reason"] == "RESOLVED"
    assert last["conflict"] is False
    assert last["decision_id"] == DECISION, last["decision_id"]
    assert last["contributions"] and last["evidence"]
    assert last["buy_total"] == 0.0 and last["sell_total"] == 1.0
    print("OK — قرار نظيف وسجلّ قابل للتدقيق:", last["decision_id"])


async def test_eligibility_buy_alone_wins():
    print("\n--- test_eligibility_buy_alone_wins ---")
    # ورقة ق٨ §١٦ (بختم NQ بند 22 حزمة ب — ب٣): شراء مؤهل وبيع غير مؤهل
    # → جانب القرار = شراء، والمنشأ eligibility.
    atom, bus = await _new()
    await atom._on_eligibility_buy(_eligibility("eligible"))
    await atom._on_eligibility_sell(_eligibility("not_eligible", "LOW_CONFIDENCE"))
    await atom._on_wait_state(_eligibility("inactive"))
    await atom._on_scored(_scored(1.0, 0.0, 1.0, score=72.0, confidence=0.8,
                                  strength=0.61, identity=IDENTITY))
    last = _last(bus)
    assert last["decision_side"] == "buy", last["decision_side"]
    assert last["resolution"]["origin"] == "eligibility", last["resolution"]
    assert last["reason"] == "ELIGIBLE_SIDE_BUY", last["reason"]
    assert last["resolution"]["buy_status"] == "eligible"
    assert last["resolution"]["sell_status"] == "not_eligible"
    assert last["resolution"]["wait_status"] == "inactive"
    assert "eligibility_missing" not in last["warnings"], last["warnings"]
    print("OK — شراء وحده مؤهل → جانب القرار شراء (منشأ eligibility)")


async def test_eligibility_sell_alone_wins():
    print("\n--- test_eligibility_sell_alone_wins ---")
    # ورقة ق٨ §١٧: بيع مؤهل وشراء غير مؤهل → جانب القرار = بيع.
    # حُدّث (v3.1.0): الحسم يشترط اكتمال الثلاثي — أُضيف حكم 457 الغائب.
    atom, bus = await _new()
    await atom._on_eligibility_buy(_eligibility("not_eligible", "LOW_CONFIDENCE"))
    await atom._on_eligibility_sell(_eligibility("eligible"))
    await atom._on_wait_state(_eligibility("inactive"))
    await atom._on_scored(_scored(0.0, 1.0, 1.0, score=68.0, confidence=0.75,
                                  strength=0.64, identity=IDENTITY))
    last = _last(bus)
    assert last["decision_side"] == "sell", last["decision_side"]
    assert last["resolution"]["origin"] == "eligibility"
    assert last["reason"] == "ELIGIBLE_SIDE_SELL", last["reason"]
    print("OK — بيع وحده مؤهل → جانب القرار بيع")


async def test_eligibility_none_is_wait():
    print("\n--- test_eligibility_none_is_wait ---")
    # ورقة ق٨ §١٨: لا أحد مؤهل → انتظار، وسبب 457 محفوظ بسجل الحسم.
    atom, bus = await _new()
    await atom._on_eligibility_buy(_eligibility("not_eligible", "LOW_STRENGTH"))
    await atom._on_eligibility_sell(_eligibility("not_eligible", "LOW_STRENGTH"))
    await atom._on_wait_state(_eligibility("eligible", "CONFIDENCE_BELOW_THRESHOLD"))
    await atom._on_scored(_scored(0.4, 0.3, 2.0, score=30.0, confidence=0.61,
                                  strength=0.35, identity=IDENTITY))
    last = _last(bus)
    assert last["decision_side"] == "wait", last["decision_side"]
    assert last["resolution"]["origin"] == "eligibility"
    assert last["reason"] == "NO_ELIGIBLE_SIDE", last["reason"]
    assert last["resolution"]["wait_reason"] == "CONFIDENCE_BELOW_THRESHOLD"
    print("OK — لا أحد مؤهل → انتظار وسبب 457 محفوظ")


async def test_eligibility_both_go_to_conflict_rule():
    print("\n--- test_eligibility_both_go_to_conflict_rule ---")
    # ورقة ق٨ §١٩ + §٩–١٠: كلاهما مؤهل ≠ شراء+بيع — قواعد التعارض القائمة
    # عند 458 تحسم وحدها (455/456 لا يحسمان)، والنتيجة قرار واحد.
    # حُدّث (v3.1.0): الحسم يشترط اكتمال الثلاثي — أُضيف حكم 457 الغائب
    # (عند تأهل الجانبين ينشر 457 حالة inactive بسبب BOTH_SIDES_ELIGIBLE).
    atom, bus = await _new()
    await atom._on_eligibility_buy(_eligibility("eligible"))
    await atom._on_eligibility_sell(_eligibility("eligible"))
    await atom._on_wait_state(_eligibility("inactive", "BOTH_SIDES_ELIGIBLE"))
    await atom._on_scored(_scored(1.0, 0.6, 2.0, score=25.0, confidence=0.8,
                                  strength=0.2, identity=IDENTITY))
    last = _last(bus)
    assert last["decision_side"] == "buy", last["decision_side"]
    assert last["resolution"]["origin"] == "conflict_rule", last["resolution"]
    assert last["resolution"]["eligibility_conflict"] is True
    assert last["reason"] == "RESOLVED_WITH_CONFLICT", last["reason"]
    sides = [last["decision_side"]]
    assert sides.count("buy") + sides.count("sell") + sides.count("wait") == 1
    print("OK — كلاهما مؤهل → حل التعارض حسم بقرار واحد:", last["decision_side"])


async def test_missing_eligibility_falls_back_with_warning():
    print("\n--- test_missing_eligibility_falls_back_with_warning ---")
    # ب٣: غياب أحداث الأهلية عن decision_id (نشر متدرج) = لا اختراع —
    # يكمل 458 بمنطقه القائم ويعلن eligibility_missing.
    # حُدّث (v3.1.0 — الحسم المقاد بالأحكام): الدورة لا تحسم فور scored بل
    # تنتظر الثلاثي؛ scored الدورة الأحدث لنفس النطاق هو ما يدفقها الآن
    # (نمط 150 المختوم: الخلف هو المهلة — لا مؤقت ولا رقم مخترع)،
    # والسلوك المعلن نفسه: fallback + إنذار eligibility_missing.
    atom, bus = await _new()
    await atom._on_scored(_scored(1.0, 0.0, 1.0, score=100.0, confidence=1.0,
                                  strength=1.0, identity=IDENTITY))
    assert not [p for n, p in bus.published if n == EVENT_OUT], \
        "دورة بمعرّف قرار لا تحسم قبل الثلاثي أو الخلف"
    await atom._on_scored(_scored(1.0, 0.0, 1.0, score=100.0, confidence=1.0,
                                  strength=1.0, identity=NEWER_IDENTITY))
    last = _last(bus)
    assert last["decision_id"] == DECISION, last["decision_id"]
    assert last["decision_side"] == "buy", last["decision_side"]
    assert last["resolution"]["origin"] == "fallback", last["resolution"]
    assert "eligibility_missing" in last["warnings"], last["warnings"]
    assert last["resolution"]["eligibility_missing"] == ["buy", "sell"]
    print("OK — بلا أهلية → الخلف يدفق بالمنطق القائم + إنذار eligibility_missing")


async def test_identity_six_fields_pass_without_loss():
    print("\n--- test_identity_six_fields_pass_without_loss ---")
    # ب١ — حكم ق٩ §١٧: كان 458 يسقط الحساب والوسيط وبداية الدورة (مسح موثق)؛
    # الآن الهوية الست تمرّ كاملة، والغائب None مع إنذار مسمّى.
    # حُدّث (v3.1.0): الثلاثي يسبق scored كي يحسم فورًا — نفس الدلالة.
    atom, bus = await _new()
    await atom._on_eligibility_buy(_eligibility("eligible"))
    await atom._on_eligibility_sell(_eligibility("not_eligible", "LOW_CONFIDENCE"))
    await atom._on_wait_state(_eligibility("inactive"))
    await atom._on_scored(_scored(1.0, 0.0, 1.0, score=90.0, confidence=1.0,
                                  strength=1.0, identity=IDENTITY))
    last = _last(bus)
    assert last["account_id"] == "ACC1" and last["broker"] == "RTL"
    assert last["symbol"] == "BTCUSD" and last["timeframe"] == "60s"
    assert last["period_start"] == 7.0 and last["decision_id"] == DECISION
    assert "identity_incomplete" not in last["warnings"], last["warnings"]
    atom2, bus2 = await _new()
    await atom2._on_scored(_scored(1.0, 0.0, 1.0, score=90.0))  # بلا هوية
    bare = _last(bus2)
    assert bare["account_id"] is None and bare["decision_id"] is None
    assert "identity_incomplete" in bare["warnings"], bare["warnings"]
    assert bare["identity_missing"] == ["account_id", "broker",
                                        "period_start", "decision_id"]
    print("OK — الهوية الست بلا فقد، والناقص معلَن بالاسم لا مخترَع")


async def test_live_bus_eligibility_lands_before_settlement():
    print("\n--- test_live_bus_eligibility_lands_before_settlement ---")
    # حُدّث لتغيير الدلالة (v3.1.0 — الحسم المقاد بالأحكام): الناقل الحقيقي
    # يوزّع مشتركي decision.scored.state بالتوازي (gather)، والتنازل التعاوني
    # القديم (sleep(0)) كان يخسر السباق أحيانًا (مقاس حيًّا: org=fallback).
    # الآن لا سباق أصلًا: مادة scored تُركن حتى يكتمل ثلاثي الأحكام
    # (455 شراء + 456 بيع + 457 انتظار) لنفس المعرّف، والحكم المكمل هو
    # الذي يحسم — مثبت هنا عبر core.event_bus الفعلي لا محاكاة.
    from core.event_bus import EventBus
    bus = EventBus()
    resolved = []
    atom = Atom()

    async def fake_455(payload):  # يشترك قبل 458 كترتيب التحميل الحي (455<458)
        await bus.publish("decision.eligibility.buy.state",
                          _eligibility("eligible"), publisher="455")

    async def fake_456(payload):
        await bus.publish("decision.eligibility.sell.state",
                          _eligibility("not_eligible", "LOW_CONFIDENCE"),
                          publisher="456")

    async def fake_457(payload):
        await bus.publish("decision.wait.state",
                          _eligibility("inactive", "BUY_SIDE_ELIGIBLE"),
                          publisher="457")

    bus.subscribe("decision.scored.state", fake_455, subscriber="455")
    bus.subscribe("decision.scored.state", fake_456, subscriber="456")
    bus.subscribe("decision.scored.state", fake_457, subscriber="457")
    await atom.initialize(AtomContext(
        458, dict(CFG), _NullLogger(),
        lambda name, payload: bus.publish(name, payload, publisher="458"),
        lambda name, handler: bus.subscribe(name, handler, subscriber="458")))
    await atom.start()
    bus.subscribe("decision.resolved.state",
                  lambda payload: resolved.append(payload), subscriber="proof")
    await bus.publish("decision.scored.state",
                      _scored(1.0, 0.0, 1.0, score=72.0, confidence=0.8,
                              strength=0.61, identity=IDENTITY), publisher="453")
    # الناقل الحقيقي (V3.0) لا يُسلِّم أثناء publish -- يودع بصناديق البريد
    # فقط ويعود؛ التسليم عبر مهام المستهلكين الخلفية. القياس بعد التسليم
    # لا بعد الإيداع (نفس الاصطلاح المستخدَم بكل اختبار ناقل حقيقي بالمشروع).
    assert await bus.drain(timeout_s=5.0)
    assert resolved, "لم يصدر قرار محلول عبر الناقل الحقيقي"
    assert len(resolved) == 1, "حسم واحد بالضبط لكل دورة scored"
    last = resolved[-1]
    assert last["resolution"]["origin"] == "eligibility", last["resolution"]
    assert last["decision_side"] == "buy", last["decision_side"]
    assert "eligibility_missing" not in last["warnings"], last["warnings"]
    print("OK — عبر الناقل الحقيقي: اكتمال الثلاثي حسم بلا سباق والمنشأ eligibility")


async def test_newer_scored_flushes_stale_and_trio_settles_newest():
    print("\n--- test_newer_scored_flushes_stale_and_trio_settles_newest ---")
    # v3.1.0 — الدفق بالأحدث (نمط 150 المختوم «newer_tick»: الخلف هو المهلة،
    # لا مؤقت ولا رقم مخترع): (١) دورة معلقة بلا أحكام تُدفق فور وصول scored
    # الأحدث لنفس النطاق — fallback المعلن نفسه بإنذار eligibility_missing؛
    # (٢) الأحدث تبقى معلقة حتى يكتمل ثلاثيها فيحسمها الحكم المكمل —
    # origin=eligibility بلا سباق، والثنائي وحده لا يكفي.
    atom, bus = await _new()
    await atom._on_scored(_scored(1.0, 0.0, 1.0, score=100.0, confidence=1.0,
                                  strength=1.0, identity=IDENTITY))
    assert not [p for n, p in bus.published if n == EVENT_OUT]
    await atom._on_scored(_scored(1.0, 0.0, 1.0, score=72.0, confidence=0.8,
                                  strength=0.61, identity=NEWER_IDENTITY))
    events = [p for n, p in bus.published if n == EVENT_OUT]
    assert len(events) == 1, "الخلف يدفق الأقدم فقط — الأحدث ما زالت معلقة"
    stale = events[0]
    assert stale["decision_id"] == DECISION, stale["decision_id"]
    assert stale["resolution"]["origin"] == "fallback", stale["resolution"]
    assert "eligibility_missing" in stale["warnings"], stale["warnings"]
    assert stale["resolution"]["eligibility_missing"] == ["buy", "sell"]
    # ثلاثي الأحدث يصل: الثنائي لا يحسم...
    await atom._on_eligibility_buy(_eligibility("eligible",
                                                decision_id=NEWER_DECISION))
    await atom._on_eligibility_sell(_eligibility("not_eligible", "LOW_CONFIDENCE",
                                                 decision_id=NEWER_DECISION))
    assert len([p for n, p in bus.published if n == EVENT_OUT]) == 1, \
        "الثنائي لا يكفي — الثلاثي شرط الحسم"
    # ...والحكم الثالث (457) يكمل الثلاثي فيحسم فورًا.
    await atom._on_wait_state(_eligibility("inactive", "BUY_SIDE_ELIGIBLE",
                                           decision_id=NEWER_DECISION))
    events = [p for n, p in bus.published if n == EVENT_OUT]
    assert len(events) == 2, "اكتمال الثلاثي يحسم الأحدث"
    newest = events[-1]
    assert newest["decision_id"] == NEWER_DECISION, newest["decision_id"]
    assert newest["resolution"]["origin"] == "eligibility", newest["resolution"]
    assert newest["decision_side"] == "buy", newest["decision_side"]
    assert "eligibility_missing" not in newest["warnings"], newest["warnings"]
    print("OK — الخلف دفق الأقدم fallback معلنًا، والثلاثي حسم الأحدث بلا سباق")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_scored(_scored(1.0, 0.0, 1.0, score=100.0, confidence=1.0, strength=1.0))
    health = await atom.health_check()
    assert health.state == HealthState.HEALTHY and "buy=1" in health.message
    print("OK — الصحة:", health.message)


async def main():
    tests = [test_no_evidence_is_wait, test_balanced_is_neutral, test_conflict_is_flagged,
             test_clean_resolution_and_record,
             test_eligibility_buy_alone_wins, test_eligibility_sell_alone_wins,
             test_eligibility_none_is_wait, test_eligibility_both_go_to_conflict_rule,
             test_missing_eligibility_falls_back_with_warning,
             test_identity_six_fields_pass_without_loss,
             test_live_bus_eligibility_lands_before_settlement,
             test_newer_scored_flushes_stale_and_trio_settles_newest,
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
