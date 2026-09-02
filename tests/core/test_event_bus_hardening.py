from __future__ import annotations

import asyncio
import time

import pytest

from core.event_bus import EventBus


@pytest.mark.asyncio
async def test_nested_payload_isolated_between_subscribers() -> None:
    bus = EventBus(); seen: list[int] = []
    def vandal(payload: dict) -> None: payload["nested"]["value"] = 9
    def observer(payload: dict) -> None: seen.append(payload["nested"]["value"])
    bus.subscribe("e", vandal, subscriber="vandal")
    bus.subscribe("e", observer, subscriber="observer")
    await bus.publish("e", {"nested": {"value": 1}})
    # (nq seal 2026-08-25: EventBus 1.18+ enqueues to per-handler mailboxes;
    # drain so both deliveries land before asserting isolation.)
    assert await bus.drain(timeout_s=5.0)
    assert seen == [1]


@pytest.mark.asyncio
async def test_synchronous_handler_timeout_does_not_block_loop() -> None:
    # ⛔ لا تُصغَّر هذه الأرقام ظنًّا أنّها مبالغة — هي مقيسة على جهاز المالك.
    #
    # دقّة مؤقّت ويندوز = 15.6 مللي (قياس حيّ: asyncio.sleep(1ms) استغرق 12–16).
    # المهلة القديمة كانت 10 مللي — أي **أصغر من قدرة النظام على القياس**، فتنطلق
    # قبل وصول أيّ نتيجة من أيّ خيط، فيُعدّ المشترك السريع متأخّرًا كذبًا ويسقط
    # الاختبار على كود سليم. (قياس: زمن to_thread لدالّة فارغة = 0.3 مللي فقط،
    # فالخيط ليس السبب — المؤقّت هو السبب.)
    #
    # النسب محفوظة كما كانت حرفيًّا:  البطيء = 5 × المهلة  ·  الحدّ = 3.5 × المهلة.
    # ولا شرط حُذف ولا عقد خُفّف — رُفعت الأرقام فقط فوق أرضيّة الجهاز.
    # سندها: دستور الذرّة قاعدة 21 «الكود ويندوز-سليم».
    #
    # وما يحرسه هذا الاختبار: المادة 5 من دستور السيادة — المعالج المتجاوز
    # للمهلة **يُعزل ويُلغى** ولا يحتجز الناقل. ممنوع `asyncio.shield` في
    # `_run_handler`؛ وجوده يُسقط هذا الاختبار فورًا (مُثبَت بالكسر 2026-08-18).
    # والقياس **نسبيّ** لا مطلق، وهذا مقصود: الشرط القديم `< 0.21` ثانية كان يسقط
    # على كود سليم كلّما انشغل الجهاز (مقيس 2026-08-18: مرّ في جولة 125 ثانية
    # وسقط في جولة 237 ثانية لنفس الكود). ما يُراد إثباته ليس رقمًا بالثواني بل
    # واقعة واحدة: **الناقل رجع قبل أن ينتهي المعالج البطيء** — أي لم ينتظره.
    # وهذه الواقعة صحيحة تحت أيّ حِمل، لأنّ الطرفين ينزلقان معًا.
    bus = EventBus(dispatch_timeout_s=0.06); seen: list[int] = []
    finished: list[float] = []
    loop = asyncio.get_running_loop()
    def slow(payload: dict) -> None:
        time.sleep(0.30); finished.append(loop.time()); payload["nested"]["value"] = 9
    bus.subscribe("e", slow, subscriber="slow")
    bus.subscribe("e", lambda payload: seen.append(payload["nested"]["value"]),
                  subscriber="observer")
    await bus.publish("e", {"nested": {"value": 1}})
    returned = loop.time()
    # (nq seal 2026-08-25: publish returns at enqueue time — the guarded fact
    # "the bus returned before the slow handler finished" is now structural.
    # Drain waits for delivery/timeout accounting before asserting.)
    assert await bus.drain(timeout_s=5.0)
    assert seen == [1]
    assert bus.stats()["timeout"]["e"] == 1
    await asyncio.sleep(0.45)  # let the isolated worker exit
    assert finished, "المعالج البطيء لم ينتهِ إطلاقًا"
    assert returned < finished[0], "الناقل انتظر المعالج البطيء بدل أن يمضي"


@pytest.mark.asyncio
async def test_timed_out_handler_releases_its_lock_immediately() -> None:
    # المادة 5 (دستور السيادة): «أي معالج يتجاوز المهلة **يُعزل ويُلغى**».
    #
    # ⚠️ هذا هو الحارس الحقيقيّ للمادة 5 — والاختبار الذي فوقه لا يحرسها:
    # مُثبَت بالكسر 2026-08-18 أنّ الاختبار الأعلى يمرّ على الكودين معًا.
    # الفرق بين `shield` وعدمه لا يظهر في الحدث الأوّل إطلاقًا — يظهر في الثاني:
    #   • بلا shield : تنتهي المهلة ⇒ يُلغى العمل ⇒ القفل يُفكّ فورًا ⇒ الحدث
    #                  التالي يمرّ طبيعيًّا.
    #   • مع shield  : تنتهي المهلة ⇒ العمل محميّ من الإلغاء ⇒ القفل محجوز حتى
    #                  ينتهي الخيط ⇒ الحدث التالي ينحبس ثمّ يسقط بمهلة.
    # فالذرّة البطيئة تخنق نفسها — وهو ما كُتبت المادة 5 لمنعه.
    #
    # الأرقام مقيسة فوق أرضيّة مؤقّت ويندوز (15.6 مللي) — لا تُصغَّر.
    bus = EventBus(dispatch_timeout_s=0.06)
    seen: list[int] = []
    calls: list[int] = []

    def slow_first_then_fast(payload: dict) -> None:
        calls.append(1)
        if len(calls) == 1:
            time.sleep(0.30)          # الحدث الأوّل وحده بطيء
        seen.append(payload["n"])

    bus.subscribe("e", slow_first_then_fast, subscriber="slow_once")

    # (nq seal 2026-08-25: the per-handler LOCK died with EventBus 1.18 —
    # the per-handler MAILBOX now carries the same Article-5 guarantee: a
    # timed-out delivery is isolated and counted, and the NEXT event to the
    # same handler flows immediately instead of queueing behind a stuck
    # lock. Same guarded fact, new mechanism; drains added because publish
    # returns at enqueue time.)
    await bus.publish("e", {"n": 1})          # يتجاوز المهلة ويُعزل
    assert await bus.drain(timeout_s=5.0)
    assert bus.stats()["timeout"]["e"] == 1

    loop = asyncio.get_running_loop()
    started = loop.time()
    await bus.publish("e", {"n": 2})          # سريع — يجب أن يمرّ فورًا
    elapsed = loop.time() - started
    assert await bus.drain(timeout_s=5.0)

    assert elapsed < 0.05, "الصندوق ما زال محبوسًا بعد المهلة — المادة 5 مكسورة"
    assert bus.stats()["timeout"]["e"] == 1, "الحدث الثاني سقط بمهلة بسبب صندوق عالق"
    assert 2 in seen, "الحدث الثاني لم يصل إلى المعالج"

    await asyncio.sleep(0.35)  # let the isolated worker exit


@pytest.mark.asyncio
async def test_non_coroutine_awaitable_is_awaited() -> None:
    bus = EventBus(dispatch_timeout_s=0.1)
    loop = asyncio.get_running_loop(); future = loop.create_future()
    loop.call_later(0.01, future.set_result, None)
    bus.subscribe("e", lambda _payload: future, subscriber="future")
    await bus.publish("e", {})
    # (nq seal 2026-08-25: mailbox delivery — drain before asserting.)
    assert await bus.drain(timeout_s=5.0)
    assert future.done()
    assert bus.stats()["delivered"]["e"] == 1
