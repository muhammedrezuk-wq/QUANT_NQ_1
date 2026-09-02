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
    "_atom463", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom463"] = _mod
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
        return AtomContext(atom_id=463, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _positions(by_symbol):
    return {"open_count": sum(by_symbol.values()), "by_symbol": dict(by_symbol)}


async def _new(max_per_symbol=1):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"max_per_symbol": max_per_symbol}))
    await atom.start()
    return atom, bus


def _for(bus, symbol):
    return [p for n, p in bus.published if n == EVENT_OUT and p["symbol"] == symbol]


async def test_held_blocks():
    print("\n--- test_held_blocks ---")
    atom, bus = await _new()
    await atom._on_positions(_positions({"XAUUSD": 1}))
    last = _for(bus, "XAUUSD")[-1]
    assert last["metadata"]["passed"] is False, last
    assert last["id"] == "position_filter"
    print("OK — ذهب محمول → block")


async def test_unheld_no_block():
    print("\n--- test_unheld_no_block ---")
    atom, bus = await _new()
    await atom._on_positions(_positions({}))
    assert _for(bus, "XAUUSD") == [], "لا يجب أن ينشر لرمز غير محمول"
    print("OK — لا مركز → لا حجب (المرور افتراضي)")


async def test_freed_passes():
    print("\n--- test_freed_passes ---")
    atom, bus = await _new()
    await atom._on_positions(_positions({"BTCUSD": 1}))
    await atom._on_positions(_positions({}))  # أُغلق المركز
    last = _for(bus, "BTCUSD")[-1]
    assert last["metadata"]["passed"] is True, last
    print("OK — أُغلق المركز → pass (يُفكّ الحجب)")


async def test_max_per_symbol():
    print("\n--- test_max_per_symbol ---")
    atom, bus = await _new(max_per_symbol=2)
    await atom._on_positions(_positions({"EURUSD": 1}))  # < السقف → لا حجب
    assert _for(bus, "EURUSD") == []
    await atom._on_positions(_positions({"EURUSD": 2}))  # = السقف → حجب
    assert _for(bus, "EURUSD")[-1]["metadata"]["passed"] is False
    print("OK — السقف قابل للضبط (2)")


async def test_health_contract():
    print("\n--- test_health_contract ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"max_per_symbol": 1}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    await atom._on_positions(_positions({"XAUUSD": 1}))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — العقد + الصحة")


def _resolved(symbol, cycle_id="c-1"):
    return {"symbol": symbol, "cycle_id": cycle_id, "signal": "buy", "score": 70}


async def test_verdict_on_demand_pass():
    # عطل حي مقيس 2026-08-19: رمز بلا مراكز = صمت، و454 يعدّ الصمت حظرًا —
    # سلسلة القرار كلها BLOCKED_UPSTREAM إلى الأبد. الحكم عند الطلب يكسر القفل.
    print("\n--- test_verdict_on_demand_pass ---")
    atom, bus = await _new()
    await atom._on_positions(_positions({}))          # صورة مراكز: لا شيء مفتوح
    await atom._on_resolved(_resolved("BTCUSD"))
    last = _for(bus, "BTCUSD")[-1]
    assert last["metadata"]["passed"] is True, last
    assert last["cycle_id"] == "c-1"
    print("OK — قرار برمز بلا مراكز → حكم مرور صريح (لا صمت)")


async def test_verdict_on_demand_block():
    print("\n--- test_verdict_on_demand_block ---")
    atom, bus = await _new()
    await atom._on_positions(_positions({"BTCUSD": 1}))
    await atom._on_resolved(_resolved("BTCUSD"))
    last = _for(bus, "BTCUSD")[-1]
    assert last["metadata"]["passed"] is False, last
    print("OK — قرار برمز محمول → حكم حظر صريح")


async def test_no_verdict_before_first_positions_picture():
    print("\n--- test_no_verdict_before_first_positions_picture ---")
    atom, bus = await _new()
    await atom._on_resolved(_resolved("BTCUSD"))
    assert _for(bus, "BTCUSD") == [], "لا حكم قبل أول صورة مراكز — لا نخترع حقيقة"
    print("OK — قبل أول صورة مراكز: لا حكم مخترع")


async def test_real_609_payload_shape_without_by_symbol():
    # عقد 609 الفعلي: قائمة مراكز لكل حساب بلا by_symbol — كان يُرمى كاملًا.
    print("\n--- test_real_609_payload_shape_without_by_symbol ---")
    atom, bus = await _new()
    await atom._on_positions({
        "account_id": "10096831", "broker": "Raw Trading Ltd",
        "positions": [
            {"ticket": 1, "symbol": "XAUUSD", "side": "buy", "volume": 0.1},
        ],
        "open_count": 1, "source": "609",
    })
    assert (await atom.health_check()).state == HealthState.HEALTHY, \
        "شكل 609 الحقيقي (بلا by_symbol) يجب أن يُفهم لا أن يُرمى"
    last = _for(bus, "XAUUSD")[-1]
    assert last["metadata"]["passed"] is False
    await atom._on_resolved(_resolved("BTCUSD"))
    assert _for(bus, "BTCUSD")[-1]["metadata"]["passed"] is True
    print("OK — شكل 609 الحقيقي مفهوم: الذهب محجوب والبيتكوين مارق")


async def test_verdicts_refresh_with_every_positions_picture():
    # ترتيب التوصيل بالناقل يجعل حكم الدورة يصل بعد تقييم 454 دومًا، ودورة
    # القرار أطول من مهلة النضارة — بلا إنعاش يُقرأ الحكم بائتًا إلى الأبد.
    print("\n--- test_verdicts_refresh_with_every_positions_picture ---")
    atom, bus = await _new()
    await atom._on_positions(_positions({}))
    await atom._on_resolved(_resolved("BTCUSD"))
    before = len(_for(bus, "BTCUSD"))
    await atom._on_positions(_positions({}))  # صورة مراكز جديدة (~كل ثانية من 609)
    after = _for(bus, "BTCUSD")
    assert len(after) == before + 1, "الحكم يجب أن يُنعَش مع كل صورة مراكز"
    assert after[-1]["metadata"]["passed"] is True
    print("OK — الحكم يتجدّد مع كل صورة مراكز: عمره ثانية دائمًا")


async def main():
    tests = [test_held_blocks, test_unheld_no_block, test_freed_passes,
             test_max_per_symbol, test_health_contract,
             test_verdict_on_demand_pass, test_verdict_on_demand_block,
             test_no_verdict_before_first_positions_picture,
             test_real_609_payload_shape_without_by_symbol,
             test_verdicts_refresh_with_every_positions_picture]
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
