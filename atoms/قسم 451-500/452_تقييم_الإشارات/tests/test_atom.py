import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom452", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom452"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"low_quality_factor": 0.5, "min_confidence": 0.0}


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
        return AtomContext(atom_id=452, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _row(source, direction="buy", kind="directional", fresh=True, confidence=0.8,
         status="ok", quality="good"):
    return {"source": source, "label": source, "kind": kind, "direction": direction,
            "score": 70, "confidence": confidence, "quality": quality,
            "fresh": fresh, "status": status}


def _payload(rows):
    return {"symbol": "BTCUSD", "timeframe": "60s", "cycle_id": "BTCUSD|60s|1.0",
            "evidence": rows, "complete": True, "cycle_status": "complete", "status": "ok"}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    return atom, bus


def _last(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


def _find(payload, source):
    for row in payload["evidence"]:
        if row["source"] == source:
            return row
    return None


async def test_reasons_are_written():
    print("\n--- test_reasons_are_written ---")
    atom, bus = await _new()
    await atom._on_aggregated(_payload([
        _row("401"),
        _row("166", fresh=False),
        _row("350:reversal_model", direction="unknown", kind="context"),
        _row("400:news_strategy", direction="none"),
        _row("400:range_strategy", status="insufficient_data"),
        _row("400:weak_strategy", confidence=0.0),
    ]))
    last = _last(bus)
    assert _find(last, "401")["eligibility_reason"] == "ELIGIBLE"
    assert _find(last, "166")["eligibility_reason"] == "STALE_CYCLE"
    assert _find(last, "350:reversal_model")["eligibility_reason"] == "CONTEXT_ONLY"
    assert _find(last, "400:news_strategy")["eligibility_reason"] == "NO_DIRECTION"
    assert _find(last, "400:range_strategy")["eligibility_reason"] == "SOURCE_NOT_OK"
    assert _find(last, "400:weak_strategy")["eligibility_reason"] == "NO_CONFIDENCE"
    assert last["eligible_count"] == 1, last["eligible_count"]
    print("OK — كل دليل معه سبب مكتوب، والمؤهّل واحد")


async def test_no_strength_is_invented():
    print("\n--- test_no_strength_is_invented ---")
    atom, bus = await _new()
    await atom._on_aggregated(_payload([_row("401", confidence=1.0)]))
    last = _last(bus)
    assert "strength" not in last, "لا يجوز أن تخترع 452 قوّة"
    for row in last["evidence"]:
        assert "strength" not in row
    print("OK — لا قوّة مخترعة من الثقة")


async def test_low_quality_is_discounted():
    print("\n--- test_low_quality_is_discounted ---")
    atom, bus = await _new()
    await atom._on_aggregated(_payload([_row("401", quality="low")]))
    row = _find(_last(bus), "401")
    assert row["eligible"] is True and row["quality_factor"] == 0.5, row
    print("OK — جودة منخفضة → معامل 0.5 لا إقصاء")


async def test_identity_six_fields_pass_without_loss():
    print("\n--- test_identity_six_fields_pass_without_loss ---")
    # بختم NQ بند 22 حزمة ب (ب١ — حكم ق٩ §١٧): الهوية الست تقرأ من المدخل
    # وتعاد بالمخرج كاملة — كان الوسيط (broker) يسقط هنا بالذات (مسح موثق).
    atom, bus = await _new()
    payload = _payload([_row("401")])
    payload.update({"account_id": "ACC1", "broker": "Raw Trading Ltd",
                    "period_start": 1.0, "decision_id": "dec:ACC1|RTL|BTCUSD|60s|1.0"})
    await atom._on_aggregated(payload)
    last = _last(bus)
    assert last["account_id"] == "ACC1"
    assert last["broker"] == "Raw Trading Ltd", "الوسيط سقط عند 452 من جديد"
    assert last["symbol"] == "BTCUSD" and last["timeframe"] == "60s"
    assert last["period_start"] == 1.0
    assert last["decision_id"] == "dec:ACC1|RTL|BTCUSD|60s|1.0"
    assert "identity_incomplete" not in last["warnings"], last["warnings"]
    assert last["identity_missing"] == [], last["identity_missing"]
    print("OK — الهوية الست مرّت بلا فقد ولا إنذار")


async def test_incomplete_identity_declared_not_invented():
    print("\n--- test_incomplete_identity_declared_not_invented ---")
    # ب١: حمولة ناقصة الهوية لا تكمَّل بالاختراع — تعاد None ويعلَن إنذار
    # identity_incomplete مع أسماء الحقول الغائبة.
    atom, bus = await _new()
    await atom._on_aggregated(_payload([_row("401")]))  # بلا حساب/وسيط/بداية/معرّف
    last = _last(bus)
    assert last["account_id"] is None and last["broker"] is None
    assert last["period_start"] is None and last["decision_id"] is None
    assert "identity_incomplete" in last["warnings"], last["warnings"]
    assert last["identity_missing"] == ["account_id", "broker",
                                        "period_start", "decision_id"], last["identity_missing"]
    print("OK — الغائب أعيد None وأعلن بالاسم، لا اختراع")


async def test_honest_none_head_no_fixed_zeros():
    print("\n--- test_honest_none_head_no_fixed_zeros ---")
    # بختم NQ بند 22 حزمة ب (ب٦): الأصفار الثابتة القديمة (سطر ~157:
    # ""/0/0.0 كانت تنشر كقياس) استبدلت بالإعلان الصادق None بنمط 451 v2.6.7 —
    # 452 لا يحسب signal/score/confidence أصلًا.
    atom, bus = await _new()
    await atom._on_aggregated(_payload([_row("401")]))
    last = _last(bus)
    assert last["signal"] is None, last["signal"]
    assert last["score"] is None, last["score"]
    assert last["confidence"] is None, last["confidence"]
    print("OK — رأس الحمولة None صادق، لا أصفار ملفقة")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await atom._on_aggregated(_payload([_row("401")]))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة")


async def test_aggregate_fields_pass_only_when_present():
    print("\n--- test_aggregate_fields_pass_only_when_present ---")
    # بختم NQ بند 22 حزمة ب — توصيل الثمانية + حكم المالك ٢٠٢٦-٠٨-٢٠ (ب NQ):
    # العمق المجمع (current_depth/required_depth/depth_unknown_fields) وحالة
    # الأقسام المجمعة (aggregate_state/state_missing_sections) تقاس عند 451
    # وتمرّ من هنا كما هي إن وُجدت — والغائب لا يُخترع (لا مفاتيح من عدم).
    atom, bus = await _new()
    payload = _payload([_row("401")])
    payload.update({"current_depth": 83.5, "required_depth": None,
                    "depth_unknown_fields": ["required_depth"],
                    "aggregate_state": "ANALYZING",
                    "state_missing_sections": ["350"]})
    await atom._on_aggregated(payload)
    last = _last(bus)
    assert last["current_depth"] == 83.5, last["current_depth"]
    assert last["required_depth"] is None
    assert last["depth_unknown_fields"] == ["required_depth"]
    assert last["aggregate_state"] == "ANALYZING"
    assert last["state_missing_sections"] == ["350"]
    await atom._on_aggregated(_payload([_row("401")]))
    bare = _last(bus)
    for key in ("current_depth", "required_depth", "depth_unknown_fields",
                "aggregate_state", "state_missing_sections"):
        assert key not in bare, key
    print("OK — الحاضر يمرّ كما هو والغائب لا يُخترع")


async def main():
    tests = [test_reasons_are_written, test_no_strength_is_invented,
             test_low_quality_is_discounted,
             test_identity_six_fields_pass_without_loss,
             test_incomplete_identity_declared_not_invented,
             test_honest_none_head_no_fixed_zeros,
             test_aggregate_fields_pass_only_when_present, test_health]
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
