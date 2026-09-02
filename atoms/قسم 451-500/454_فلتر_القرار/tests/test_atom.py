import asyncio
import os
import sys
import time

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
    "_atom454", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom454"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"min_score": 60}


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
        return AtomContext(atom_id=454, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _scored(direction, score):
    return {"symbol": "NQ100", "timeframe": "60s", "cycle_id": "NQ100|60s|0.0",
            "signal": direction, "score": score}


def _filter(fid, passed):
    return {"symbol": "NQ100", "timeframe": "60s", "cycle_id": "NQ100|60s|0.0", "id": fid,
            "metadata": {"passed": passed}}


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    await atom.start()
    for fid in _mod._FILTER_IDS:
        await atom._on_filter({"symbol": "NQ100", "timeframe": "60s", "cycle_id": "NQ100|60s|0.0", "id": fid, "metadata": {"passed": True}})
    await atom._on_calendar({"known": True, "in_event_window": False})
    await atom._on_quality({"symbol": "NQ100", "status": "HEALTHY"})
    await atom._on_feed({"status": "ACTIVE"})
    return atom, bus


def _last(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


async def test_pass_high():
    print("\n--- test_pass_high ---")
    atom, bus = await _new()
    await atom._on_scored(_scored("buy", 80))
    assert _last(bus)["metadata"]["passed"] is True
    print("OK — buy 80 → passed")


async def test_block_low():
    print("\n--- test_block_low ---")
    atom, bus = await _new()
    await atom._on_scored(_scored("buy", 50))
    assert _last(bus)["metadata"]["passed"] is False
    print("OK — buy 50 → not passed")


async def test_block_wait_and_health():
    print("\n--- test_block_wait_and_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    await atom._on_scored(_scored("wait", 100))
    last = _last(bus)
    assert last["metadata"]["passed"] is False
    assert last["id"] == "decision_filter"
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — wait مهما الدرجة → not passed + الصحة")


async def test_filter_blocks_high_score():
    print("\n--- test_filter_blocks_high_score ---")
    atom, bus = await _new()
    await atom._on_filter(_filter("confidence_filter", False))
    await atom._on_scored(_scored("buy", 90))
    last = _last(bus)
    assert last["metadata"]["passed"] is False, "الفلتر يمنع رغم score=90"
    assert "confidence_filter" in last["metadata"]["blocked_by"]
    print("OK — فلتر ثقة يمنع رغم score=90")


async def test_late_decision_finds_its_own_cycle_verdicts():
    # عطل حي مقيس 2026-08-19: مخزن «آخر حكم فقط» يُداس بحكم الدورة التالية قبل
    # وصول قرار الدورة الحالية عبر خط الأنابيب (452→453→458) — فكل قرار يُحكم
    # «دورة مغايرة» كلما سرُعت الدورات (mism على الفلاتر الأربعة، passed=0).
    print("\n--- test_late_decision_finds_its_own_cycle_verdicts ---")
    atom, bus = await _new()  # أحكام الدورة A محفوظة
    for fid in _mod._FILTER_IDS:  # أحكام الدورة B تصل قبل قرار A
        await atom._on_filter({"symbol": "NQ100", "timeframe": "60s",
                               "cycle_id": "NQ100|60s|60.0", "id": fid,
                               "metadata": {"passed": True}})
    await atom._on_scored(_scored("buy", 80))  # قرار الدورة A يصل الآن
    last = _last(bus)
    assert last["metadata"]["passed"] is True, (
        "قرار الدورة A لم يجد أحكام دورته المحفوظة بعد وصول أحكام B: %s"
        % last["metadata"])
    print("OK — قرار متأخر بالأنبوب يجد أحكام دورته: لا دوس بعد اليوم")


def _news_window(symbol="NQ100", grade="high", phase="after", block=True,
                 start_offset=-60.0, end_offset=840.0, headline="CPI beats"):
    now = time.time()
    return {"symbol": symbol, "grade": grade, "phase": phase,
            "window_start": now + start_offset, "window_end": now + end_offset,
            "headline": headline, "source": "test", "block": block}


async def test_news_window_blocks():
    # بختم NQ بند 22 حزمة أ — ق٧: block=true ضمن النافذة يحجب باسم news_window.
    print("\n--- test_news_window_blocks ---")
    atom, bus = await _new()
    await atom._on_news_window(_news_window())
    await atom._on_scored(_scored("buy", 90))
    last = _last(bus)
    assert last["metadata"]["passed"] is False, last["metadata"]
    assert "news_window" in last["metadata"]["blocked_by"], last["metadata"]["blocked_by"]
    detail = last["metadata"]["news_window"]
    assert detail["grade"] == "high" and detail["phase"] == "after"
    assert detail["window_end"] > time.time()
    assert last["metadata"]["news_window_blocked"] is True
    print("OK — نافذة خبر عالية تحجب رغم score=90 مع تفاصيلها")


async def test_news_window_expired_or_unblocking_passes():
    print("\n--- test_news_window_expired_or_unblocking_passes ---")
    atom, bus = await _new()
    # نافذة منقضية: انتهت قبل الآن — لا حجب (الحاجز يزول بانتهاء window_end).
    await atom._on_news_window(_news_window(end_offset=-1.0, headline="old"))
    await atom._on_scored(_scored("buy", 80))
    last = _last(bus)
    assert last["metadata"]["passed"] is True, last["metadata"]["blocked_by"]
    # حدث block=false (درجة بلا نافذة مالك) لا يحجب.
    await atom._on_news_window(_news_window(grade="medium", block=False,
                                            headline="soft"))
    await atom._on_scored(_scored("buy", 80))
    last = _last(bus)
    assert last["metadata"]["passed"] is True, last["metadata"]["blocked_by"]
    assert last["metadata"]["news_window_blocked"] is False
    print("OK — نافذة منقضية أو بلا حظر لا تحجب: الحجب حصرًا ضمن نافذة معلنة")


async def test_barriers_carry_value_threshold_reason_time():
    print("\n--- test_barriers_carry_value_threshold_reason_time ---")
    # بختم NQ بند 22 حزمة ب (ب٧): كل حاجز يعلنه 454 يحمل الرباعية
    # value/threshold/reason/measured_at بقائمة barriers الموحدة، إضافة
    # لـblocked_by القائم (توافقًا).
    atom, bus = await _new()
    before = time.time()
    await atom._on_scored(_scored("buy", 50))  # دون عتبة الدرجة 60
    last = _last(bus)
    assert last["metadata"]["passed"] is False
    assert "score_gate" in last["metadata"]["blocked_by"]
    rows = last["barriers"]
    assert isinstance(rows, list) and rows, rows
    names = [row["name"] for row in rows]
    assert names == last["metadata"]["blocked_by"], (names, last["metadata"]["blocked_by"])
    gate = rows[names.index("score_gate")]
    assert gate["value"] == 50.0, gate
    assert gate["threshold"] == 60.0, gate
    assert gate["reason"] == "SCORE_BELOW_MIN", gate
    assert isinstance(gate["measured_at"], float) and gate["measured_at"] >= before, gate
    for row in rows:
        assert set(row) == {"name", "value", "threshold", "reason", "measured_at"}, row
    print("OK — حاجز الدرجة بالرباعية كاملة والقائمة تطابق blocked_by")


async def test_news_window_barrier_quad():
    print("\n--- test_news_window_barrier_quad ---")
    # ب٧ على حاجز الأخبار (ق٧): نافذة حظر تعلن الرباعية أيضًا.
    atom, bus = await _new()
    await atom._on_news_window(_news_window())
    await atom._on_scored(_scored("buy", 90))
    last = _last(bus)
    rows = {row["name"]: row for row in last["barriers"]}
    assert "news_window" in rows, last["barriers"]
    news = rows["news_window"]
    assert news["value"] == "high", news
    assert news["reason"] == "NEWS_WINDOW_BLOCK", news
    assert isinstance(news["measured_at"], float), news
    assert news["threshold"] is None, news  # لا عتبة رقمية للنافذة — None صادقة
    print("OK — حاجز نافذة الخبر يحمل الرباعية")


async def test_unknown_score_blocks_explicitly():
    print("\n--- test_unknown_score_blocks_explicitly ---")
    # بختم NQ بند 22 حزمة ب (ب٦): المجهول ليس صفرًا معلومًا — درجة غائبة كانت
    # تتحول 0 وتجتاز عتبة 0 بالصدفة (min_score=0 هو الساري بالمانيفست اليوم).
    # الآن تحجب بسبب صريح score_unknown.
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"min_score": 0}))
    await atom.start()
    for fid in _mod._FILTER_IDS:
        await atom._on_filter({"symbol": "NQ100", "timeframe": "60s",
                               "cycle_id": "NQ100|60s|0.0", "id": fid,
                               "metadata": {"passed": True}})
    await atom._on_calendar({"known": True, "in_event_window": False})
    await atom._on_quality({"symbol": "NQ100", "status": "HEALTHY"})
    await atom._on_feed({"status": "ACTIVE"})
    payload = _scored("buy", 0)
    payload["score"] = None
    await atom._on_scored(payload)
    last = _last(bus)
    assert last["metadata"]["passed"] is False, "درجة مجهولة اجتازت العتبة بالصدفة"
    assert "score_unknown" in last["metadata"]["blocked_by"], last["metadata"]["blocked_by"]
    row = [r for r in last["barriers"] if r["name"] == "score_unknown"][0]
    assert row["value"] is None and row["threshold"] == 0.0
    assert row["reason"] == "SCORE_UNKNOWN"
    assert last["score"] is None, last["score"]  # لا يُنشر صفر ملفق
    print("OK — المجهول حُجب بسببه الصريح ولم يُصفَّر")


async def test_unknown_side_blocks_explicitly():
    print("\n--- test_unknown_side_blocks_explicitly ---")
    # ب٦: جانب قرار غائب/غير معروف ليس انتظارًا معلومًا — يحجب باسمه.
    atom, bus = await _new()
    payload = _scored("", 90)
    payload["signal"] = ""
    await atom._on_scored(payload)
    last = _last(bus)
    assert last["metadata"]["passed"] is False
    assert "decision_side_unknown" in last["metadata"]["blocked_by"]
    assert last["decision_side"] is None, last["decision_side"]
    print("OK — جانب مجهول حُجب بـdecision_side_unknown لا بانتظار مزعوم")


async def test_decision_side_and_identity_pass_through():
    print("\n--- test_decision_side_and_identity_pass_through ---")
    # ب١ (حكم ق٩ §١٧) + ب٦: الهوية الست تمرّ كاملة، وdecision_side من مدخل
    # 458 هو جانب القرار المعتمد (الأسبق على signal القديم).
    atom, bus = await _new()
    payload = _scored("neutral", 80)  # signal قديم متناقض عمدًا
    payload.update({"decision_side": "buy", "account_id": "ACC1", "broker": "RTL",
                    "period_start": 0.0, "decision_id": "dec:ACC1|RTL|NQ100|60s|0.0"})
    await atom._on_scored(payload)
    last = _last(bus)
    assert last["decision_side"] == "buy", last["decision_side"]
    assert last["metadata"]["passed"] is True, last["metadata"]["blocked_by"]
    assert last["account_id"] == "ACC1" and last["broker"] == "RTL"
    assert last["period_start"] == 0.0
    assert last["decision_id"] == "dec:ACC1|RTL|NQ100|60s|0.0"
    assert "identity_incomplete" not in last["warnings"], last["warnings"]
    atom2, bus2 = await _new()
    await atom2._on_scored(_scored("buy", 80))  # بلا هوية — الناقص يعلَن
    bare = _last(bus2)
    assert "identity_incomplete" in bare["warnings"], bare["warnings"]
    assert bare["identity_missing"] == ["account_id", "broker",
                                        "period_start", "decision_id"]
    print("OK — decision_side يقود البوابة والهوية الست بلا فقد")


async def main():
    tests = [test_pass_high, test_block_low, test_block_wait_and_health,
             test_filter_blocks_high_score,
             test_late_decision_finds_its_own_cycle_verdicts,
             test_news_window_blocks,
             test_news_window_expired_or_unblocking_passes,
             test_barriers_carry_value_threshold_reason_time,
             test_news_window_barrier_quad,
             test_unknown_score_blocks_explicitly,
             test_unknown_side_blocks_explicitly,
             test_decision_side_and_identity_pass_through]
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
