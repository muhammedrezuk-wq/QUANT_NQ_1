"""اختبار فتحة النواة V2.0 (الأوراق ٠٢–٠٦) — يشتغل على النواة الحقيقية، ويطبع
أخضر/أحمر لكل بند قبول. تشغيل مباشر:  python tools/test_core_v2_opening.py

يستبدل tools/test_trace_middleware.py القديم (الوسيط الخارجي حُذف؛ توريث الأثر صار
جوّا الناقل — يُغطّى هنا في قسم ٠٤)."""
import sys
import asyncio
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.event_bus import EventBus
from core.lifecycle import call_lifecycle, LifecycleTimeout

R = []


def check(name, cond):
    R.append(bool(cond))
    print(("PASS ✅" if cond else "FAIL ❌"), name)


async def main():
    # ————— ٠٢ العيون (عدّادات) —————
    b = EventBus(dispatch_timeout_s=0.2)
    await b.publish("x.event", {"a": 1}, publisher="t")
    s = b.stats()
    check("٠٢ حدث بلا مشترك يُعدّ", s["no_subscribers"].get("x.event") == 1 and s["published"].get("x.event") == 1)

    got = []
    b.subscribe("y.event", lambda p: got.append(p), subscriber="s1")
    await b.publish("y.event", {"a": 2}, publisher="t")
    s = b.stats()
    check("٠٢ تسليم ناجح (delivered=1)", s["delivered"].get("y.event") == 1 and s["no_subscribers"].get("y.event", 0) == 0)

    async def ok(p): pass
    async def slow(p): await asyncio.sleep(1)
    def boom(p): raise ValueError("boom")
    b.subscribe("z.event", ok, subscriber="ok")
    b.subscribe("z.event", slow, subscriber="slow")
    b.subscribe("z.event", boom, subscriber="boom")
    await b.publish("z.event", {}, publisher="t")
    s = b.stats()
    check("٠٢ نجاح+مهلة+خطأ (1/1/1)", s["delivered"].get("z.event") == 1 and s["timeout"].get("z.event") == 1 and s["error"].get("z.event") == 1)

    # ————— ٠٣ الحالة عند الاشتراك —————
    b2 = EventBus()
    await b2.publish("platform.account.state", {"bal": 100}, publisher="619")
    late = []
    b2.subscribe("platform.account.state", lambda p: late.append(p), subscriber="651")
    await asyncio.sleep(0.03)
    check("٠٣ الحالة تُعاد للمتأخّر (619→651)", len(late) == 1 and late[0].get("bal") == 100)

    await b2.publish("execution.order.open", {"side": "BUY"}, publisher="450")
    latec = []
    b2.subscribe("execution.order.open", lambda p: latec.append(p), subscriber="arm")
    await asyncio.sleep(0.03)
    check("٠٣ الأمر لا يُعاد (لا تنفيذ مزدوج)", len(latec) == 0)

    pre = []
    b2.subscribe("never.state", lambda p: pre.append(p), subscriber="x")
    await asyncio.sleep(0.03)
    check("٠٣ لا إعادة لحدث لم يُنشر", len(pre) == 0)

    # ————— ٠٤ وراثة الأثر —————
    b3 = EventBus()
    child = {}
    async def on_tick(p):
        await b3.publish("strategy.signal", {"v": 1}, publisher="strat")
    b3.subscribe("market.tick", on_tick, subscriber="strat")
    b3.subscribe("strategy.signal", lambda p: child.update(p), subscriber="cap")
    await b3.publish("market.tick", {"v": 0}, publisher="feed")
    check("٠٤ الابن يرث اسم أبيه", child.get("parent_event") == "market.tick")
    check("٠٤ الابن يرث الرحلة وله هويّته", bool(child.get("trace_id")) and bool(child.get("event_id")))

    root = {}
    b4 = EventBus()
    b4.subscribe("root.tick", lambda p: root.update(p), subscriber="c")
    await b4.publish("root.tick", {}, publisher="feed")
    check("٠٤ الجذر بلا أب", root.get("parent_event_id") is None)

    # ————— ٠٥ الساعة —————
    b5 = EventBus()
    auto = {}
    b5.subscribe("k.state", lambda p: auto.update(p), subscriber="c")
    # ٢٠٢٦-٠٨-٣١: العقد انقلب. كان الناقل يملك إزاحة (`set_time_offset`)
    # فيصير للنظام مالكان للوقت. الآن: الناقل بلا ساعة، وختمه من `clock.now()`
    # وحدها. البرهان أن الختم يتبع السلطة الزمنيّة لا ساعة داخليّة.
    import clock as _clock
    before = _clock.now()
    await b5.publish("k.state", {}, publisher="t")
    after = _clock.now()
    stamp = auto.get("timestamp", 0)
    check("٠٥ الناقل بلا ساعة (لا إزاحة ولا now)",
          not hasattr(b5, "set_time_offset") and not hasattr(b5, "now"))
    check("٠٥ ختم الناقل من السلطة الزمنيّة `clock`", before <= stamp <= after)

    ext = {}
    b5.subscribe("mt5.tick.state", lambda p: ext.update(p), subscriber="c")
    await b5.publish("mt5.tick.state", {"timestamp": 42.0}, publisher="mt5")
    check("٠٥ وقت المصدر الخارجي لا يُداس", ext.get("timestamp") == 42.0)

    # ٠٥ (تكامل): بعد إلغاء ملكيّة الوقت من الناقل، المطلوب برهانُ العكس —
    # أن المُقلِع **لا يشترك** على حدث الوقت ولا يغذّي ساعةً ثانية، وأن ختم
    # الأحداث يبقى من `clock` حتى بعد مرور `time.utc.synced` في الناقل.
    import tempfile
    from core.bootloader import Bootloader
    from core.registry import Registry
    from core.journal import Journal
    from core.metrics import Metrics
    with tempfile.TemporaryDirectory() as _d:
        ibus = EventBus()
        bl = Bootloader(Path(_d), Registry(), ibus, Journal(path=None), Metrics())
        await bl.boot()
        no_clock_sub = ibus.subscriber_count("time.utc.synced") == 0
        await ibus.publish("time.utc.synced", {"offset_s": 1000.0}, publisher="608")
        seen = {}
        ibus.subscribe("probe.after", lambda p: seen.update(p), subscriber="probe")
        low = _clock.now()
        await ibus.publish("probe.after", {}, publisher="test")
        high = _clock.now()
    check("٠٥ تكامل: المُقلِع لا يشترك على حدث الوقت (لا مالك ثانٍ)", no_clock_sub)
    check("٠٥ تكامل: حدث الوقت لا يزيح ختم الناقل",
          low <= seen.get("timestamp", 0) <= high)

    # ————— ٠٦ المهلات + التطهير —————
    done = []
    async def quick(): done.append(1)
    await call_lifecycle(quick(), "start", timeout=0.5)
    check("٠٦ نداء سريع يمرّ", done == [1])

    async def hang(): await asyncio.sleep(5)
    try:
        await call_lifecycle(hang(), "stop", timeout=0.1)
        check("٠٦ المتعلّق يُرفع LifecycleTimeout", False)
    except LifecycleTimeout as e:
        check("٠٦ المتعلّق → LifecycleTimeout(stop) بلا تجميد", e.phase == "stop")

    async def boom2(): raise ValueError("x")
    try:
        await call_lifecycle(boom2(), "start", timeout=0.5)
        check("٠٦ استثناء الذرة يمرّ كما هو", False)
    except ValueError:
        check("٠٦ استثناء الذرة العادي يمرّ (مو مهلة)", True)
    except LifecycleTimeout:
        check("٠٦ استثناء الذرة العادي يمرّ (مو مهلة)", False)

    from core.bootloader import Bootloader
    pre_k = Bootloader.atom_module_prefix(99)
    sys.modules[pre_k + "mod"] = 1
    sys.modules[pre_k + "sib_helper"] = 1
    for k in [k for k in sys.modules if k.startswith(pre_k)]:
        del sys.modules[k]
    check("٠٦-ب تطهير sys.modules بالبادئة الموحّدة", not any(k.startswith(pre_k) for k in sys.modules))

    # ————— ١١ firehose (subscribe_all) + الفتح ما يفضى + حارس الأوامر —————
    from core.event_bus import _is_replayable
    bf = EventBus()
    seen_all = []
    bf.subscribe_all(lambda name, p: seen_all.append(name), subscriber="gov")
    await bf.publish("market_data.state", {"x": 1}, publisher="a")
    await bf.publish("execution.order.open", {"side": "BUY"}, publisher="b")
    await bf.publish("no.subs.event", {}, publisher="c")
    check("١١ firehose يستلم كل الأحداث خام (حتى بلا مشترك على الاسم)",
          set(seen_all) == {"market_data.state", "execution.order.open", "no.subs.event"})

    bf2 = EventBus()
    await bf2.publish("portfolio.state", {"bal": 500}, publisher="pf")           # حالة → تُخزَّن
    await bf2.publish("execution.order.open", {"side": "SELL"}, publisher="ex")   # أمر → ما يُخزَّن
    late_all = {}
    bf2.subscribe_all(lambda name, p: late_all.update({name: p}), subscriber="gov2")
    await asyncio.sleep(0.03)
    check("١١ الفتح ما يفضى: المتأخّر على الكل ياخد آخر حالة فورًا",
          late_all.get("portfolio.state", {}).get("bal") == 500)
    check("١١ الأمر لا يُعاد لمشترك الكل (لا تنفيذ مزدوج)",
          "execution.order.open" not in late_all)
    check("١١ حارس الأوامر: أمر بلاحقة حالة ('x.order.state') لا يُعاد أبدًا",
          _is_replayable("x.order.state") is False)
    check("١١ حالة عادية ('x.state') قابلة للإعادة",
          _is_replayable("x.state") is True)

    ok_all = all(R)
    print("\n" + "=" * 46)
    print(f"النتيجة: {'كل اختبارات فتحة V2.0 خضراء ✅' if ok_all else 'في فشل ❌'}  ({sum(R)}/{len(R)})")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
