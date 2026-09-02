"""
Core.event_bus — الناقل: توجيهٌ فقط، بلا ساعة وبلا تنفيذ على حلقة التنسيق.
============================================================================
Article 10 (+ Article 7 في الدستور الأول): يعرف الأحداث فقط، لا الذرات.
لا يسمح بالتواصل المباشر بين الذرات (Article 23) — كل تواصل يمر من هنا.

ضمانات المادة 89 (حظر تعطيل الـ Event Bus المشترك):
  * كل معالج يُنفَّذ معزولًا: استثناؤه يُلتقط ولا يمسّ بقية المشتركين.
  * كل معالج محكوم بمهلة قصوى (`dispatch_timeout_s`).
  * كل مشترك يستلم **نسخته الخاصة** من الحمولة (المادة 30/35).
ضمان المادة 31: تُحقن الحقول المعيارية تلقائيًا إن غابت.

—— فتحة النواة V2.0 (أوراق ٠٢–٠٥ و١١) ——
  ٠٢ العيون: عدّادات خام لكل حدث + `stats()` لقطة قراءة فقط.
  ٠٣ الحالة عند الاشتراك: يُحفظ آخر حدث «حالة» ويُعاد فورًا للمشترك الجديد،
     فالمتأخّر لا يفوته آخر واقع. والأوامر لا تُعاد أبدًا.
  ٠٤ وراثة الأثر: كل حدث يرث `trace_id` أبيه ويسجّل `parent_event_id`.
  ١١ firehose: `subscribe_all` يبثّ كل حدث خام لطبقة ٢ بلا تفسير.

—— V3.0 (ختم nq · 2026-08-25) — صناديق البريد ——
  الجذر المقيس: `publish` كان ينتظر كل مشتركيه (`gather`)، فمستمع بطيء واحد
  (مقيس: 30ث × ثلاث ذرّات تخزين) يحبس الناشر — والناشر المحبوس هو تغذية
  السوق نفسها (مقيس: 88 م.ب مرميّة في جلسة). العلاج: صندوق بريد لكل معالج
  (طابور + مستهلك واحد) — النشر إيداعٌ فوريّ، والتسليم بالترتيب، بلا حجز.

—— V3.1 (ختم nq · 2026-08-25) — تقنين التنازل ——
  التنازل بعد كل نشرة كان يحدّ أسرع ناشر بسرعة دورة الطابور (مقيس: ٩ رسائل/ث
  لمضخّة FIX → رمي 868KB/70ث بلا انقطاع شبكة). صار مرّة كل نافذة قصيرة
  (`_YIELD_EVERY_S`)، وعلامة الضغط تتجاوز النافذة عند احتقان صندوق.

—— 1.31.0 (ختم nq · 2026-08-31) — توحيد خطّي عمل متوازيين ——
  الجذر المقيس: **الوقت كان مملوكًا مرّتين** — `OfficialClock` المستقلّة،
  وإزاحةٌ داخل الناقل تُغذّى من حدث `time.utc.synced`. فصارت صحّة الساعة
  تابعة لجدولة استهلاك الأحداث. القياس: 806 ينشر `SYS_SECOND` بمعدّل
  1.000/ث، والناقل يقول `dropped=0 · timeout=0 · delivered=3714`، ومع ذلك
  يصل الطابع بتأخّر **تراكميّ** (٣٫٩٧ث ← ٦٠٫٥٦ث ← ٩٧٫٨٧ث خلال ست عشرة
  دقيقة ≈ ٠٫١–٠٫١٥ ث/ث) — فيخرج `age_s` سالبًا على صفٍّ عمره ثانيتان،
  وتُعلَن بياناتٌ طازجة «قديمة».
  * الناقل لا يملك ساعة إطلاقًا: لا إزاحة ولا `set_time_offset` ولا `now()`.
    كل ختم زمنيّ من `clock.now()`، وكل فرق زمن ومهلة من الساعة الرتيبة.
    و`time.utc.synced` صار **إعلانًا لا مصدرًا** — الذرّة 003 تصحّح الساعة
    بـ`clock.accept_sample` مباشرة، فتأخّر الإعلان لا يؤخّر الساعة.
  * بِركتا خيوط محدودتان للمعالج المتزامن، وواحدة **محجوزة** للوقت والحالة
    والأوامر كي لا يبتلع ضغطُ أحداث السوق كامل السعة.
  * النشر من حلقة غير حلقة النواة يُعاد توجيهه إليها
    (`run_coroutine_threadsafe`) بدل لمس صناديق البريد عبر الحلقات.
  * المعالج **غير المتزامن** يبقى على حلقة النواة: كائنات asyncio التي
    أنشأها في `initialize/start` مربوطة بها (مقيس: ٢٦ ذرّة `create_task`،
    ٥ `Lock`، ٣ `Event`، ٨ `get_running_loop`).
  * ميزانية زمن لكل تسليم (`_LIGHT_BUDGET_S`): التجاوز يُعدّ ويُعلَن، ومعه
    `oldest_pending_age_s` — لأن طول الطابور وحده لا يميّز واقعة عمرها
    ١ ملّي من أخرى عمرها ٣٠ ثانية، وذلك العمى هو ما جعل تأخّر الاستهلاك
    يتنكّر في هيئة «بيانات قديمة».
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import os
import pickle
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from clock import now as official_now
from core.logger import current_event, current_event_id, current_trace_id

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]
GlobalHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]

_log = logging.getLogger("quant_nq.core.event_bus")
DEFAULT_DISPATCH_TIMEOUT_S = 30.0
DEFAULT_MAILBOX_MAX_EVENTS = 1024
_YIELD_EVERY_S = 0.002
_PRESSURE_MARK = DEFAULT_MAILBOX_MAX_EVENTS // 2
_STATE_SUFFIXES = (".state", ".synced", ".snapshot")
_COMMAND_MARKERS = ("order", ".buy", ".sell", ".execute", ".cancel", "final_decision", "command", ".submit", ".send")
_TIME_SIGNAL_EVENTS = frozenset({"SYS_SECOND", "SYS_5MIN", "SYS_15MIN", "SYS_HOUR", "SYS_DAY"})
_NAME_CLASS_CACHE: dict[str, tuple[bool, bool]] = {}
_NAME_CLASS_CACHE_MAX = 4096
_GENERAL_WORKERS_MIN = 4
_GENERAL_WORKERS_MAX = 28
_REALTIME_WORKERS = 4
#: ميزانية زمن التسليم الواحد. التجاوز ليس خطأً ولا يوقف شيئًا — يُعدّ
#: ويُعلَن في `stats()["overrun"]` باسم الحدث وأسوأ مدّة، فيُعرَف الشغل
#: الحاجز **بالرقم** قبل نقله لمسار ثقيل. (قانون المالك: قِس أوّلًا.)
_LIGHT_BUDGET_S = 0.05


def _classify_name(event_name: str) -> tuple[bool, bool]:
    cached = _NAME_CLASS_CACHE.get(event_name)
    if cached is None:
        command = any(marker in event_name for marker in _COMMAND_MARKERS)
        replayable = event_name.endswith(_STATE_SUFFIXES) and not command
        if len(_NAME_CLASS_CACHE) >= _NAME_CLASS_CACHE_MAX:
            _NAME_CLASS_CACHE.clear()
        cached = (command, replayable)
        _NAME_CLASS_CACHE[event_name] = cached
    return cached


def _is_command(event_name: str) -> bool:
    return _classify_name(event_name)[0]


def _is_replayable(event_name: str) -> bool:
    return event_name in _TIME_SIGNAL_EVENTS or _classify_name(event_name)[1]


def _is_realtime(event_name: str) -> bool:
    return event_name in _TIME_SIGNAL_EVENTS or _is_command(event_name) or event_name.endswith(_STATE_SUFFIXES)


def _fast_copy(value: Any) -> Any:
    try:
        return pickle.loads(pickle.dumps(value, pickle.HIGHEST_PROTOCOL))
    except Exception:  # noqa: BLE001
        return copy.deepcopy(value)


def _coalesce_key(event_name: str, payload: Any) -> tuple[Any, ...]:
    """مفتاح دمج الحالة: الاسم + نطاق الحمولة — لا يُدمَج عبر النطاقات.

    ٢٠٢٦-٠٨-٣١ (توحيد الشغلين): كانت دالّة وحدة (module-level) ثم صارت
    `@staticmethod` داخل الصنف، فانكسر استيرادها في `transport/ownership.py`
    (`ImportError: cannot import name '_coalesce_key'`) وسقطت طبقة ناقل
    الأحداث كلّها عند التجميع. رجعت وحدةً عامّة — والصنف يناديها كما هي."""
    if not isinstance(payload, dict):
        return (event_name,)
    return (
        event_name,
        str(payload.get("account_id") or ""),
        str(payload.get("symbol") or ""),
        str(
            payload.get("section_id")
            or payload.get("analyzer_id")
            or payload.get("strategy_id")
            or payload.get("id")
            or ""
        ),
    )


def _worker_entry(handler: Callable[..., Any], args: tuple[Any, ...]) -> Any:
    """مدخل الخيط: للمعالج **المتزامن** فقط.

    ٢٠٢٦-٠٨-٣١ (ختم nq — توحيد الشغلين): كان هنا `asyncio.run(result)`، أي
    **حلقة أحداث جديدة تُنشأ وتُهدم مع كل تسليم** لكل معالج غير متزامن.
    مقيس على شجرتنا: ٢٦ ذرّة تستعمل `asyncio.create_task`، و٥ `asyncio.Lock()`،
    و٣ `asyncio.Event()`، و٨ `get_running_loop` — وكلّها كائنات تُنشأ على
    حلقة النواة في `initialize/start`. أوّل لمسة لها من حلقةٍ أخرى =
    `RuntimeError: bound to a different event loop` أو تعليق صامت؛ وأي
    `create_task` داخل معالج كان يُقتل فور عودة المعالج لأن `asyncio.run`
    تهدم حلقتها. فبقي المعالج غير المتزامن على **حلقة النواة** كما كان،
    وعزلُ الشغل الثقيل يبقى محكومًا بالقياس (`overrun` أدناه) لا بالتخمين."""
    return handler(*args)


@dataclass(slots=True)
class _Subscription:
    handler: Handler
    subscriber: str = ""
    is_coro: bool = False
    isolate: bool = True


@dataclass(slots=True)
class _Mailbox:
    queue: deque = field(default_factory=deque)
    wakeup: asyncio.Event | None = None
    task: asyncio.Task | None = None
    busy: bool = False


class EventBus:
    """Routing only; handler execution is isolated from the Core event loop.

    Two bounded execution pools are used so general market-event pressure
    cannot consume all worker capacity reserved for time/state/command work.
    Each handler still has one mailbox consumer, preserving per-handler order.
    """

    def __init__(self, *, dispatch_timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
                 mailbox_max_events: int = DEFAULT_MAILBOX_MAX_EVENTS) -> None:
        self._subscribers: dict[str, list[_Subscription]] = defaultdict(list)
        self._global_subscribers: list[tuple[GlobalHandler, str, bool, bool]] = []
        self._dispatch_timeout_s = float(dispatch_timeout_s)
        self._mailbox_max_events = max(1, int(mailbox_max_events))
        self._mailboxes: dict[int, _Mailbox] = {}
        self._handler_refs: dict[int, int] = {}
        self._last_event: dict[str, dict[str, Any]] = {}
        self._published: dict[str, int] = defaultdict(int)
        self._delivered: dict[str, int] = defaultdict(int)
        self._no_subscribers: dict[str, int] = defaultdict(int)
        self._timeout: dict[str, int] = defaultdict(int)
        self._error: dict[str, int] = defaultdict(int)
        self._replayed: dict[str, int] = defaultdict(int)
        self._dropped: dict[str, int] = defaultdict(int)
        self._coalesced: dict[str, int] = defaultdict(int)
        # بند ٨/١٢: تجاوز ميزانية التسليم — عدّاد وأسوأ مدّة لكل حدث.
        self._overrun: dict[str, int] = defaultdict(int)
        self._overrun_worst_s: dict[str, float] = {}
        self._last_yield = 0.0
        self._yield_pressure = False
        self._core_loop: asyncio.AbstractEventLoop | None = None
        cpu_workers = max(1, (os.cpu_count() or 1) + 4)
        general_workers = max(_GENERAL_WORKERS_MIN, min(_GENERAL_WORKERS_MAX, cpu_workers))
        self._handler_executor = ThreadPoolExecutor(
            max_workers=general_workers, thread_name_prefix="quant-event-handler"
        )
        self._realtime_executor = ThreadPoolExecutor(
            max_workers=_REALTIME_WORKERS, thread_name_prefix="quant-event-realtime"
        )

    def _bind_core_loop(self) -> asyncio.AbstractEventLoop | None:
        """يثبّت حلقة النواة عند أول استعمال داخل حلقة، ويمنع خلط حلقتين.

        ٢٠٢٦-٠٨-٣١ (توحيد الشغلين): كانت تنادي `get_running_loop()` مباشرة،
        فأي `subscribe`/`unsubscribe` **خارج** حلقة تشغيل يرفع
        `RuntimeError: no running event loop`. والاشتراك خارج الحلقة سلوكٌ
        مشروع وقائم (ذرّات تشترك في `initialize` قبل الإقلاع، وفحوصٌ تبني
        الناقل بلا حلقة). الربط صار كسولًا: بلا حلقة = لا ربط ولا خطأ،
        والحلقة تُثبَّت عند أوّل عمل حقيقي داخلها."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        if self._core_loop is None or self._core_loop.is_closed():
            self._core_loop = loop
        elif self._core_loop is not loop:
            raise RuntimeError("EventBus used from multiple core event loops")
        return loop

    def _handler_ref_add(self, handler_id: int, count: int = 1) -> None:
        self._handler_refs[handler_id] = self._handler_refs.get(handler_id, 0) + count

    def _handler_ref_drop(self, handler_id: int, count: int = 1) -> None:
        remaining = self._handler_refs.get(handler_id, 0) - count
        if remaining > 0:
            self._handler_refs[handler_id] = remaining
            return
        self._handler_refs.pop(handler_id, None)
        self._retire_mailbox(handler_id)

    def _handler_is_active(self, handler_id: int) -> bool:
        return self._handler_refs.get(handler_id, 0) > 0

    def _retire_mailbox(self, handler_id: int) -> None:
        box = self._mailboxes.pop(handler_id, None)
        if box is not None and box.task is not None and not box.task.done():
            box.task.cancel()

    def _mailbox_of(self, handler_id: int) -> _Mailbox:
        box = self._mailboxes.get(handler_id)
        if box is None:
            box = self._mailboxes[handler_id] = _Mailbox()
        return box

    _coalesce_key = staticmethod(_coalesce_key)

    def _enqueue(self, handler_id: int, item: tuple[Any, ...], event_name: str) -> None:
        box = self._mailbox_of(handler_id)
        # بند ١٢: كل واقعة تحمل لحظة إيداعها. طولُ الطابور وحده لا يقول شيئًا —
        # طابور فيه عنصر واحد قد يكون عمره ١ ملّي أو ٣٠ ثانية، والفرق هو كل
        # الفرق. `oldest_pending_age_s` هو المقياس الذي كان غيابه يجعل تأخّر
        # الاستهلاك يتنكّر في هيئة «بيانات قديمة» عند المستهلكين.
        item = (*item, time.perf_counter())
        if _is_replayable(event_name):
            key = self._coalesce_key(event_name, item[2])
            for index in range(len(box.queue) - 1, -1, -1):
                pending = box.queue[index]
                if pending[1] == event_name and self._coalesce_key(event_name, pending[2]) == key:
                    # الأحدث يحلّ محلّ الأقدم — ويرث **لحظة إيداع الأقدم**، وإلّا
                    # صار الدمج يخفي عمر الانتظار الحقيقي فيكذب المقياس نفسه.
                    box.queue[index] = (*item[:-1], pending[-1])
                    self._coalesced[event_name] += 1
                    if box.wakeup is not None:
                        box.wakeup.set()
                    return
        if not _is_command(event_name):
            while len(box.queue) >= self._mailbox_max_events:
                oldest = box.queue.popleft()
                self._dropped[str(oldest[1])] += 1
        box.queue.append(item)
        if len(box.queue) >= _PRESSURE_MARK:
            self._yield_pressure = True
        if box.wakeup is None:
            box.wakeup = asyncio.Event()
        if box.task is None or box.task.done():
            box.task = asyncio.get_running_loop().create_task(self._consume(handler_id, box))
        box.wakeup.set()

    async def _consume(self, handler_id: int, box: _Mailbox) -> None:
        try:
            while True:
                if not box.queue:
                    if not self._handler_is_active(handler_id):
                        return
                    if box.wakeup is None:
                        box.wakeup = asyncio.Event()
                    box.wakeup.clear()
                    await box.wakeup.wait()
                    continue
                kind, event_name, payload, extra, _enqueued_at = box.queue.popleft()
                box.busy = True
                try:
                    if kind == "sub":
                        await self._deliver(extra, event_name, payload)
                    else:
                        handler, subscriber, is_coro = extra
                        await self._deliver_global(handler, subscriber, is_coro, event_name, payload)
                finally:
                    box.busy = False
        except asyncio.CancelledError:
            return

    async def _deliver(self, sub: _Subscription, event_name: str, payload: dict[str, Any]) -> None:
        try:
            await self._invoke(sub, event_name, payload)
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            self._timeout[event_name] += 1
            _log.error("handler timeout subscriber=%s event=%s", sub.subscriber, event_name)
        except BaseException as error:  # noqa: BLE001
            self._error[event_name] += 1
            _log.error(
                "handler error subscriber=%s event=%s error=%s",
                sub.subscriber,
                event_name,
                error,
                exc_info=error,
            )
        else:
            self._delivered[event_name] += 1

    async def _deliver_global(
        self,
        handler: GlobalHandler,
        subscriber: str,
        is_coro: bool,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            await self._invoke_global(handler, subscriber, is_coro, event_name, payload)
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            self._timeout[event_name] += 1
            _log.error("global handler timeout subscriber=%s event=%s", subscriber, event_name)
        except BaseException as error:  # noqa: BLE001
            self._error[event_name] += 1
            _log.error(
                "global handler error subscriber=%s event=%s error=%s",
                subscriber,
                event_name,
                error,
                exc_info=error,
            )
        else:
            self._delivered[event_name] += 1

    async def _run_handler(
        self,
        handler: Callable[..., Any],
        is_coro: bool,
        event_name: str,
        *args: Any,
    ) -> None:
        # مسارا التنفيذ (ورقة المالك، بند ٣):
        #  • المعالج **المتزامن** (`def`) → بِركة خيوط محدودة، وبِركة الوقت
        #    والحالة والأوامر محجوزة عن ضغط أحداث السوق العامّة.
        #  • المعالج **غير المتزامن** (`async def`) → حلقة النواة كما هو،
        #    لأن كائنات asyncio التي أنشأها في `initialize/start` مربوطة بها.
        # وميزانية زمن لكل تسليم: التجاوز يُعدّ ويُعلَن (`overrun`) باسم
        # الحدث — فينكشف الشغل الحاجز بالرقم بدل أن يظهر كبيانات قديمة.
        started = time.perf_counter()
        try:
            if is_coro:
                async with asyncio.timeout(self._dispatch_timeout_s):
                    await handler(*args)
            else:
                loop = asyncio.get_running_loop()
                context = copy_context()
                executor = (self._realtime_executor if _is_realtime(event_name)
                            else self._handler_executor)
                async with asyncio.timeout(self._dispatch_timeout_s):
                    result = await loop.run_in_executor(
                        executor,
                        lambda: context.run(_worker_entry, handler, args),
                    )
                if inspect.isawaitable(result):
                    async with asyncio.timeout(self._dispatch_timeout_s):
                        await result
        finally:
            elapsed = time.perf_counter() - started
            if elapsed > _LIGHT_BUDGET_S:
                self._overrun[event_name] += 1
                if elapsed > self._overrun_worst_s.get(event_name, 0.0):
                    self._overrun_worst_s[event_name] = elapsed

    def subscribe(
        self,
        event_name: str,
        handler: Handler,
        *,
        subscriber: str = "",
        isolate_payload: bool = True,
    ) -> None:
        self._bind_core_loop()
        self._subscribers[event_name].append(
            _Subscription(handler, subscriber, inspect.iscoroutinefunction(handler), bool(isolate_payload))
        )
        self._handler_ref_add(id(handler))
        last = self._last_event.get(event_name)
        if last is not None:
            sub = self._subscribers[event_name][-1]
            self._replayed[event_name] += 1
            self._enqueue(id(handler), ("sub", event_name, _fast_copy(last), sub), event_name)

    def unsubscribe(self, event_name: str, handler: Handler) -> None:
        self._bind_core_loop()
        subs = self._subscribers.get(event_name)
        if subs is None:
            return
        kept = [s for s in subs if s.handler is not handler]
        removed = len(subs) - len(kept)
        if kept:
            self._subscribers[event_name] = kept
        else:
            del self._subscribers[event_name]
        if removed:
            self._handler_ref_drop(id(handler), removed)

    def unsubscribe_all(self, subscriber: str) -> int:
        self._bind_core_loop()
        removed = 0
        refs: dict[int, int] = {}
        for event_name in list(self._subscribers):
            subs = self._subscribers[event_name]
            kept = []
            for sub in subs:
                if sub.subscriber == subscriber:
                    refs[id(sub.handler)] = refs.get(id(sub.handler), 0) + 1
                else:
                    kept.append(sub)
            removed += len(subs) - len(kept)
            if kept:
                self._subscribers[event_name] = kept
            else:
                del self._subscribers[event_name]
        for handler_id, count in refs.items():
            self._handler_ref_drop(handler_id, count)
        return removed

    def subscribe_all(
        self,
        handler: GlobalHandler,
        *,
        subscriber: str = "",
        isolate_payload: bool = True,
    ) -> None:
        self._bind_core_loop()
        is_coro = inspect.iscoroutinefunction(handler)
        self._global_subscribers.append((handler, subscriber, bool(isolate_payload), is_coro))
        self._handler_ref_add(id(handler))
        for event_name, last in tuple(self._last_event.items()):
            self._replayed[event_name] += 1
            self._enqueue(
                id(handler),
                (
                    "global",
                    event_name,
                    _fast_copy(last) if isolate_payload else last,
                    (handler, subscriber, is_coro),
                ),
                event_name,
            )

    def unsubscribe_global(self, handler: GlobalHandler) -> None:
        self._bind_core_loop()
        rows = [row for row in self._global_subscribers if row[0] is handler]
        self._global_subscribers = [row for row in self._global_subscribers if row[0] is not handler]
        if rows:
            self._handler_ref_drop(id(handler), len(rows))

    async def publish(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
        *,
        publisher: str = "",
    ) -> None:
        caller = asyncio.get_running_loop()
        core = self._core_loop
        if core is None:
            self._core_loop = caller
            core = caller
        if caller is not core:
            future = asyncio.run_coroutine_threadsafe(
                self._publish_core(event_name, payload, publisher=publisher),
                core,
            )
            await asyncio.wrap_future(future)
            return
        await self._publish_core(event_name, payload, publisher=publisher)

    async def _publish_core(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
        *,
        publisher: str = "",
    ) -> None:
        base = _fast_copy(payload or {})
        base.setdefault("source", publisher)
        base.setdefault("event_id", str(uuid.uuid4()))
        base.setdefault("trace_id", current_trace_id.get() or str(uuid.uuid4()))
        base.setdefault("parent_event_id", current_event_id.get())
        base.setdefault("parent_event", current_event.get())
        base.setdefault("timestamp", official_now())
        self._published[event_name] += 1
        blob: bytes | None = None
        blob_ready = False

        def isolated_copy() -> Any:
            nonlocal blob, blob_ready
            if not blob_ready:
                blob_ready = True
                try:
                    blob = pickle.dumps(base, pickle.HIGHEST_PROTOCOL)
                except Exception:  # noqa: BLE001
                    blob = None
            return pickle.loads(blob) if blob is not None else copy.deepcopy(base)

        if _is_replayable(event_name):
            self._last_event[event_name] = isolated_copy()
        subs = tuple(self._subscribers.get(event_name, ()))
        for handler, subscriber, isolate, is_coro in tuple(self._global_subscribers):
            self._enqueue(
                id(handler),
                (
                    "global",
                    event_name,
                    isolated_copy() if isolate else base,
                    (handler, subscriber, is_coro),
                ),
                event_name,
            )
        if not subs:
            self._no_subscribers[event_name] += 1
        else:
            for sub in subs:
                self._enqueue(
                    id(sub.handler),
                    (
                        "sub",
                        event_name,
                        isolated_copy() if sub.isolate else base,
                        sub,
                    ),
                    event_name,
                )
        await self._maybe_yield()

    async def _maybe_yield(self) -> None:
        now = time.perf_counter()
        if self._yield_pressure or now - self._last_yield >= _YIELD_EVERY_S:
            self._yield_pressure = False
            self._last_yield = now
            await asyncio.sleep(0)

    async def drain(self, timeout_s: float | None = None) -> bool:
        self._bind_core_loop()
        deadline = time.monotonic() + timeout_s if timeout_s is not None else None
        while True:
            if not any(box.queue or box.busy for box in self._mailboxes.values()):
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.001)

    async def _invoke(self, sub: _Subscription, event_name: str, payload: dict[str, Any]) -> None:
        t_trace = current_trace_id.set(payload.get("trace_id"))
        t_eid = current_event_id.set(payload.get("event_id"))
        t_ev = current_event.set(event_name)
        try:
            await self._run_handler(sub.handler, sub.is_coro, event_name, payload)
        finally:
            current_event.reset(t_ev)
            current_event_id.reset(t_eid)
            current_trace_id.reset(t_trace)

    async def _invoke_global(
        self,
        handler: GlobalHandler,
        subscriber: str,
        is_coro: bool,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        t_trace = current_trace_id.set(payload.get("trace_id"))
        t_eid = current_event_id.set(payload.get("event_id"))
        t_ev = current_event.set(event_name)
        try:
            await self._run_handler(handler, is_coro, event_name, event_name, payload)
        finally:
            current_event.reset(t_ev)
            current_event_id.reset(t_eid)
            current_trace_id.reset(t_trace)

    def stats(self) -> dict[str, Any]:
        """لقطة قراءة فقط — حقائق خام بلا منطق أعمال (ورقة ٠٢).

        ٢٠٢٦-٠٨-٣١: أُضيف نبض صحّة الناقل نفسه (بند ١٢). قبله كان لا بدّ من
        انتظار 619 كي نعرف أن النظام متأخّر — والتأخّر كان يصل إلينا مُقنَّعًا
        على شكل «بيانات قديمة» بدل «استهلاك متأخّر»."""
        now = time.perf_counter()
        depth = 0
        oldest = 0.0
        busy = 0
        for box in self._mailboxes.values():
            depth += len(box.queue)
            if box.busy:
                busy += 1
            if box.queue:
                age = now - box.queue[0][-1]
                if age > oldest:
                    oldest = age
        return {
            "published": dict(self._published),
            "delivered": dict(self._delivered),
            "no_subscribers": dict(self._no_subscribers),
            "timeout": dict(self._timeout),
            "error": dict(self._error),
            "replayed": dict(self._replayed),
            "dropped": dict(self._dropped),
            "coalesced": dict(self._coalesced),
            "overrun": dict(self._overrun),
            "overrun_worst_s": {k: round(v, 4) for k, v in self._overrun_worst_s.items()},
            "pressure": {
                "mailboxes": len(self._mailboxes),
                "queued": depth,
                "busy_handlers": busy,
                "oldest_pending_age_s": round(oldest, 4),
                "light_budget_s": _LIGHT_BUDGET_S,
            },
        }

    def last_states(self) -> list[tuple[str, dict[str, Any]]]:
        return [(name, _fast_copy(last)) for name, last in self._last_event.items()]

    def event_names(self) -> list[str]:
        return list(self._subscribers.keys())

    def subscriber_count(self, event_name: str) -> int:
        return len(self._subscribers.get(event_name, ()))

    def close(self) -> None:
        self._realtime_executor.shutdown(wait=False, cancel_futures=True)
        self._handler_executor.shutdown(wait=False, cancel_futures=True)
