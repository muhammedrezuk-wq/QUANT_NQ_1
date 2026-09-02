"""إصدار Core الحالي.

يُحدَّث هذا الثابت فقط عند إصدار نسخة معمارية جديدة من Core
(Article 1: V2, V3 ...). لا علاقة له بإصدار أي ذرة.

1.1.0: تبني CORE_CONSTITUTION_V1.1 — إزالة `family` كحقل مخصص في
Manifest واستبداله بـ `metadata` عام لا يعتمد عليه Core (Article 7).

1.2.0: إضافة `AtomBase.shutdown()` — مرحلة إنهاء نهائية غير قابلة
للعكس، منفصلة عمدًا عن `stop()` القابلة لإعادة التشغيل. قرار من داخل
Core نفسها (يعالج خلطًا محتملًا بين "إيقاف عابر أثناء Restart" و"إنهاء
فعلي عند إغلاق العملية")، وليس استجابة لمستند خارجي. متوافق خلفيًا
تمامًا: التنفيذ الافتراضي لا شيء، فلا حاجة لتعديل أي ذرة موجودة.

1.3.0: `EventBus.publish()` تقبل الآن `publisher` اختيارية (افتراضي
فارغ — متوافق خلفيًا تمامًا)، تُستخدم للتسجيل الداخلي فقط (لا تصل أبدًا
لأي مشترك — Article 17/18 لم تتغيّر). `AtomContext.publish` لكل ذرة
يربطها بمعرّف الذرة تلقائيًا الآن، تمامًا كما كان `subscribe` يفعل
مسبقًا مع المشترك — يعالج تناقضًا داخليًا حقيقيًا. سجل فشل أي معالج حدث
يحمل الآن traceback كاملاً (`exc_info`) بدل نص الاستثناء فقط.

1.4.0: **إصلاح شامل معمق قبل التجميد.** العقد العام (AtomBase،
AtomContext، AtomManifest، أسماء الأحداث) ثابت تمامًا دون كسر — كل ذرة
تعمل على 1.3.0 تعمل على 1.4.0 بلا تعديل (المادة 64/65). التغييرات
داخلية وسلوكية:
  * المادة 21/81 — Bootloader لم يعد يُجهض الإقلاع بسبب ذرة حرجة.
    الذرة الحرجة الفاشلة تُستبعد وحدها ويكمل Core إقلاع الباقي.
  * المادة 89 — مهلة قصوى لكل معالج حدث: مشترك معلّق لا يجمّد الناقل.
  * المادة 31 — حقن `source` في كل حمولة حدث بجانب trace_id/timestamp.
  * المادة 30/35 — كل مشترك يستلم نسخته الخاصة من الحمولة.
  * المادة 15/86 — تطهير كامل (Registry + Health + Event Bus + الموارد)
    عند فشل الإقلاع أو فشل التحميل الحي أو السحب الحي.
  * المادة 86 — إزالة نمو الذاكرة غير المحدود في Metrics و Journal.
  * المادة 63/70 — `core/contracts/services.py`: واجهات مجردة رسمية
    لكل خدمة عامة.
  * كتابة اللقطات صارت ذرّية (os.replace) ومحكومة بمهلة.

1.5.0: **دعم الذرة متعددة الملفات** — إصلاح خطأ معماري مثبت بالدليل
(الفصل 20 من المرجع الأعلى يجيز فتح النواة لهذا السبب حصرًا). الذرة
التي تشحن ملفات مساعدة كانت مضطرة لحقن `sys.path` بنفسها لتستوردها،
وهو تلوّث دائم لبيئة المفسّر تمنعه المادة 30 — ومن لم تفعل فشل تحميلها
كليًا. الآن يُضاف مجلد الذرة لمسار البحث **أثناء التنفيذ فقط** ثم
يُسحب، وتُعاد فهرسة كل موديول مساعد تحت مفتاح خاص بذرته حتى لا تستورد
ذرةٌ كودَ أخرى عند تطابق أسماء الملفات (المادة 4/6/43). العقد العام
لم يتغيّر: لا توقيع ولا حقل ولا اسم حدث.

1.6.0: **إكمال المادة 14** — كانت تنص على "إضافة أو حذف أو تعديل أو
ترقية" ذرة أثناء التشغيل، وكان المنفَّذ الإضافة والحذف فقط:
  * ترقية حية في المكان: تغيّر إصدار المانيفست يُطلق سحبًا ثم تحميلًا
    للكود الجديد. فشل الترقية يُسقط تلك الذرة وحدها (المادة 21/81).
  * المانيفست وحده لم يعد يصنع ذرة: `scan()` يتحقق من وجود ملف نقطة
    الدخول فعليًا. حذف ملف الكود وترك المانيفست كان يُبقي الذرة تعمل
    من الذاكرة إلى الأبد دون أن تُسحب (المادة 15).
  * اشتقاق مسار نقطة الدخول توحّد في `manifest_loader.entrypoint_file`
    ليستعمله المُحمِّل والمُشكِّل معًا (المادة 42/62).

1.7.0: **فتحة النواة الواحدة (V2.0)** — الأوراق ٠٢–٠٧ دفعة واحدة. (رقمها 1.7.0
لا 2.0.0: كل تغييرات الفتحة متوافقة خلفيًا — لا كسر عقد — فهي رفعة صغيرة؛ أمّا
2.0.0 فيرفض كل ذرة تشترط core_version<2.0.0 فتُستبعد عند الإقلاع. الختم الذي
رافق الترقيم 2.0.0 أُزيح إلى CORE.lock.retired_2026-08-04، وقرار إعادة الختم
يبقى للمالك — تصحيح 2026-08-04.) التغييرات:
  * ٠٢ العيون — عدّادات خام بالناقل (نُشر/سُلّم/بلا مشترك/مهلة/خطأ/أُعيد) + stats().
  * ٠٣ الحالة عند الاشتراك — الناقل يعيد آخر حدث "حالة" للمشترك المتأخّر (يفكّ
    619→651→413)؛ الأوامر لا تُعاد أبدًا. معيار "الحالة" باللاحقة لا بقائمة يدوية.
  * ٠٤ وراثة الأثر — trace_id/event_id/parent_event_id تُختَم بالناقل (شجرة سببية).
  * ٠٥ الساعة — ختم الناقل = الوقت المصحّح (خام + إزاحة)، ولا يدوس وقت مصدر خارجي.
  * ٠٦ المهلات — call_lifecycle موحّدة بمهلة لكل نداء دورة حياة خارج الإقلاع
    (hot_reload/health/api)؛ تطهير sys.modules عند السحب؛ Bootloader يحترم
    startup_mode؛ خُطّاف تشغيل الذرة الموقّفة (POST /api/atoms/{id}/start).
  * ٠٧ إرجاع الالتفافات — توريث الأثر رجع جوّا الناقل، وأُبطِل الوسيط الخارجي
    scripts/trace_middleware.py (ما عاد يُستدعى، صار جذعًا مهجورًا)؛ المُشغّل صار
    «تشغيل + توصيل» فقط.

1.8.0: **طبقة ٢ — المرحلة ١ (ورقة ١١): النواة تتعرّى وتفتح للحوكمة.** متوافقة
خلفيًا بالكامل (لا كسر عقد — رفعة صغيرة، تبقى < 2.0.0 فالذرات تتوافق):
  * تعرية العرض — أُزيل مسار HTML من `api/app.py` وملف اللوحة من `core/`؛ النواة
    API خام + بثّ بس. العلَم `enable_dashboard` → `enable_api`.
  * firehose — `EventBus.subscribe_all`: بثّ كل حدث خام لطبقة ٢ (برّا العملية)
    بلا تفسير (النواة تبقى غبية)، بمهلة/عزل لكل مشترك.
  * توسيع replay ٠٣ — المشترك على الكل ياخد آخر حالة لكل تدفّق عند الاتصال (لا
    تفضى اللوحة عند الفتح)؛ وحارس أوامر (`_COMMAND_MARKERS`): أي حدث فيه أثر أمر
    لا يُخزَّن ولا يُعاد أبدًا (خطّ أمان ضد التنفيذ المزدوج).
  * أثر الأوامر — أفعال التحكّم (إيقاف/تشغيل) عبر API تُسجَّل بالجورنال (الحوكمة
    بلا ذاكرة، فالأثر يبقى بالنواة).

1.13.0: **استعادة المادة 5 — بأمر المالك المباشر 2026-08-18.** متوافقة خلفيًا
بالكامل (لا عقد تغيّر، ولا واجهة، ولا اسم حدث — فتبقى < 2.0.0):
  * 1.10.1 أضافت `asyncio.shield` داخل `_run_handler`، فصار المعالج المتجاوز
    للمهلة **محميًّا من الإلغاء** ويحتجز قفل ترتيبه حتى ينتهي خيطه. وهذا يقلب
    المادة 5 من دستور السيادة نصًّا: «أيّ معالج يتجاوز هذه المهلة يُعزل — يُسجّل
    الخطأ ويُلغى». أُزيل `shield` وأُزيل تأجيل فكّ القفل: الناقل لا ينتظر أحدًا.
  * التحسينات التقنيّة الستّ الأخرى (1.9.0 · 1.10.0 · 1.10.2 · 1.10.3 · 1.11.0 ·
    1.12.0) بقيت كما هي بلا مساس — المالك أقرّها بعد عرضها عليه.
  * حارسها: `tests/core/test_event_bus_hardening.py`
    ::test_timed_out_handler_releases_its_lock_immediately — مُثبَت بالكسر أنّه
    **يسقط** على نسخة `shield` ويمرّ على المصحّحة. (والاختبار الأقدم في نفس
    الملفّ لا يفرّق بينهما إطلاقًا — لا يُعتمد عليه حارسًا للمادة 5.)
  * الأختام 1.9.0→1.12.0 وُضعت بلا إذن المالك تحت أرقام متتالية؛ هذا الختم
    برقم جديد صريح ليبقى التغيير **مرئيًّا بالسجلّ** لا مطموسًا فوق بصمة سابقة.

1.14.0: **subscribe_all للذرّات — بأمر المالك المباشر 2026-08-19 (عيون ذرّة
NQ المشرفة).** متوافقة خلفيًا بالكامل (< 2.0.0، لا كسر عقد):
  * حقل اختياري جديد `AtomContext.subscribe_all` (افتراضي None — كل بناء
    قائم للعقد في الاختبارات والفحوص يعمل بلا أي تعديل) يوصل الذرّة
    بـ`EventBus.subscribe_all` القائمة أصلًا منذ 1.8.0 لطبقة ٢ خارج
    العملية — الآن متاحة للذرّات أيضًا، بنفس عزل ومهلات كل مشترك.
  * وُصّلت في المسارين: `bootloader` (الإقلاع) و`hot_reload_service`
    (التحميل/الترقية الحية) — الذرّة المعاد تحميلها تحصل عليها كذلك.
  * حارسها: `tests/core/test_subscribe_all_contract.py`.
  * وفي الفتحة نفسها — سبعة إصلاحات بمحرّك الاكتشاف الحي (مصدرها مراجعة
    خارجية عرضها المالك، تحقّقنا منها بندًا بندًا: ٧ صحيحة من ١١ فأُصلحت،
    و٣ رُفتت بالدليل وواحدة جزئية):
      ١) الحلقة الدورية لا تموت بصمت عند أول خطأ فحص — تسجّل وتُكمِل.
      ٢) ترتيب السحب فرز طوبولوجي حقيقي حتمي (كان عدّ جيران يخطئ بالسلاسل ≥3).
      ٣) حلّ اعتماديات الجدد يستبعد ما سيُسحب في الفحص نفسه (لا قبول يتيم).
      ٤) الترقية لا تطهّر موديولات القديمة قبل نجاح البديلة — rollback ببيئة كاملة.
      ٥) فشل shutdown القديمة بعد ترقية ناجحة = تحذير، لا قلب النجاح فشلًا.
      ٦) stop() لا تُستدعى على ذرة لم تبلغ start (عقد دورة الحياة).
      ٧) خطوات تطهير السحب معزولة + تحذيرات الرفض بذاكرة (لا إغراق سجل كل ٥ثوانٍ)
         + تطهير موديولات المحاولة الفاشلة.
    حرّاسها الستة الجدد في `tests/core/test_hot_reload_correctness.py`.
"""

# 1.9.0: durability and isolation hardening — state restore is part of boot and
# hot-upgrade lifecycles; dependency failures propagate fail-closed; EventBus
# isolates nested payloads, awaits every awaitable, serializes each subscriber,
# and keeps synchronous handlers off the event loop with bounded waits.
# 1.10.0: authenticated browser WebSocket handshakes can carry the API key in
# a negotiated subprotocol (browsers cannot set custom WebSocket headers).
# 1.10.1: timed-out synchronous handlers retain their ordering lock until the
# worker really exits; stale per-handler locks are pruned on unsubscribe.
# 1.10.2: hot upgrade now follows stop → snapshot → shutdown and refuses to
# start the replacement when a state-preserving unload fails.
# 1.10.3: REST API-key comparison is constant-time, matching the WebSocket
# and governance authentication paths.
# 1.11.0: WebSocket event envelopes now carry a monotonic stream sequence and
# explicit gap/drop metadata when the bounded client queue overflows.
# 1.12.0: failed hot upgrades reattach the stopped old instance and restore its
# snapshot before restart; the old instance is finalized only after commit.
# 1.15.0: main-loop throughput surgery for full multi-symbol tick flow (py-spy
# measured 2026-08-19 under seven live symbols): plain-data fast copy replaces
# copy.deepcopy on every payload hand-off (same per-subscriber isolation
# guarantee, exotic types still deepcopy); handler-activity checks are O(1)
# refcounts instead of scanning all subscriptions per invocation; the periodic
# atom rescan is gated by an off-loop disk fingerprint (unchanged disk = no
# YAML parse; manual /api/rescan always forces) and the full scan itself runs
# in a worker thread; WebSocket firehose frames are single-pass compact JSON
# and permessage-deflate is disabled at the server (local dashboard channel).
# 1.16.0: second measured round of the same surgery: payload isolation copies
# now ride a C-speed pickle round-trip (deepcopy stays the fallback for
# unpicklable payloads); the WebSocket firehose is one shared broadcaster —
# the event body is JSON-encoded once regardless of client count, each client
# queue appends its per-client stream_sequence/gap fields as cheap string
# suffixes (1.11.0 envelope contract preserved), late joiners receive stored
# last-states via the new EventBus.last_states(); subscribe_all gained an
# explicit isolate_payload=False covenant for read-only broadcasters.
# 1.17.0 (owner-authorized open, «عدل» 2026-08-19): dispatch machinery diet —
# asyncio.timeout replaces the per-delivery wait_for wrappers (same TimeoutError
# and isolation guarantees, no wrapper Task per delivery), handler coroutine-ness
# is computed once at subscribe time instead of reflected on every delivery,
# ordering locks are created once (no per-delivery Lock allocation churn), and a
# free lock is acquired without arming a timeout timer.
# 1.18.0 (owner-authorized open, «nq» 2026-08-25): publisher decoupling —
# per-handler MAILBOXES replace gather+per-handler locks. publish() enqueues
# and returns (a slow subscriber can no longer block the market feed: measured
# 30s storage listeners pinned 622's queue at its threshold, 88 MB discarded
# in one session); one consumer task per handler preserves the exact delivery
# ordering the old lock guaranteed; timeout+isolation per delivery unchanged
# (Article 89); full mailboxes jump-to-tail dropping the OLDEST with a counted,
# stats()-visible `dropped` (the sealed feed law: the live line carries no
# backlog — jump and declare); command-marked events are never dropped;
# state replay for late subscribers rides the same mailbox path; drain()
# added so tests measure after delivery, not after enqueue.
# 1.19.0 (same owner-authorized open, «nq» + «لا تقفل ما خلصنا» 2026-08-25):
# state-event coalescing (LATEST_ONLY) — an undelivered state event in a
# handler's mailbox is replaced IN PLACE by the newer one (slow consumers
# read the latest truth, not a backlog of its past; ordering across
# different events preserved; commands/facts never coalesced; counted in
# stats()["coalesced"]); and the read-only covenant is generalized:
# subscribe(..., isolate_payload=False) hands the reference without a copy
# to a subscriber that declares it reads and never mutates (default stays
# the isolated copy, Articles 30/35).
# 1.19.1 (same open): coalesce key is SCOPED (event x account x symbol x
# speaker) -- name-only coalescing could overwrite one symbol's pending state
# with another symbol's (core-engineer correction, measured on 50 symbols).
# 1.20.0 (same open, 2026-08-25): cooperative yield is TIME-BATCHED, not
# per-publish. Yielding after every publish priced each publish at one full
# ready-queue round on a busy loop -- measured: the FIX pump processed 9
# messages/second (~109ms each), its transport pinned at the 131072 cap,
# 868KB discarded per 70s with zero reconnects. Enqueue is O(1); the loop
# gets its turn at most every _YIELD_EVERY_S (2ms), which keeps consumers
# fed without capping the fastest publisher at task-switch speed.
# 1.20.1 (same open, 2026-08-25): event-name classification is computed once
# per name (memoized) -- py-spy measured the per-publish _COMMAND_MARKERS
# string scan at 36% of the main thread under the freed feed (275 of 762
# samples in 8s). Classification semantics unchanged to the letter.
# 1.21.0 (same open, 2026-08-25, adversarial review): (a) the yield window
# clock is perf_counter, not monotonic -- on Windows monotonic resolves at
# 15.625ms so the sealed 2ms window really ran at ~12.5-15.6ms (measured:
# 8 yields instead of ~50 per 100ms burst); (b) mailbox PRESSURE forces an
# immediate yield when any box crosses half its cap -- a busy 300ms publish
# burst was measured dropping 93.6% of a slow consumer's fact events
# (oldest evicted before its first turn) and growing a command box 16x.
# 1.21.1 (same open, 2026-08-25): payloads are serialized ONCE per publish
# (dumps paid once, each isolated subscriber gets its own loads) -- py-spy
# after the freed feed measured per-subscriber _fast_copy at 46% of the
# main thread under the big section cards. Article 30/35 guarantee intact:
# every subscriber still receives an independent copy; unpicklable payloads
# still fall back to deepcopy.
# 1.22.0 (unified ownership release, 2026-08-26): manifest discovery follows
# directory links on Python 3.13 and via a portable os.walk(followlinks=True)
# fallback on Python 3.12. This is required for atoms_crypto's zero-copy shared
# atom links; duplicate IDs are still rejected by the same scan contract.
# 1.23.0 (unified control ingress, 2026-08-27): the core API exposes one
# allowlisted event endpoint for crypto universe controls only. It cannot emit
# trading/execution events; requests still travel through EventBus to owner 1001.
# 1.24.0 (unified dashboard controls, 2026-08-27): governance may request only
# the two allowlisted crypto universe events through the API, preserving the
# single owner boundary and keeping trading/execution events unreachable.
#
# 1.25.0 (boundary contract, 2026-08-27): the Core API control ingress is now
# domain-neutral. Domain allowlists are injected by an external runner adapter;
# Core rejects the route closed when no adapter is present. This removes Crypto
# vocabulary from Core executable code without changing atom lifecycle contracts.
#
# 1.26.0 (boot phase contract, 2026-08-27): Bootloader.boot(include_ids=...)
# permits the external runner to load non-execution atoms first, evaluate its
# execution policy, then load the selected execution atoms in the same registry.
# The selector is generic; Core contains no execution or domain vocabulary.
#
# 1.27.0 (hot reload fingerprint contract, 2026-08-27): Runtime discovery now
# fingerprints every file in each atom directory and hashes manifests/source
# files, so same-size version changes and immediate code deletion cannot be
# skipped by filesystem timestamp granularity. No atom contract changed.
# 1.27.1 (bootloader journal fix, 2026-08-28, owner order «حلها وفك تجميد»):
# _mark_failed recorded start_failed twice per real failure (journal + metrics
# doubled). Duplicate calls removed; no behavioral contract changed.
# 1.28.0 (owner-authorized core open, «NQ — افتح النواة» 2026-08-31): the bus
# exposes its own raw counters. EventBus.stats() has existed since the Eyes
# paper (published/delivered/no_subscribers/timeout/error/replayed/dropped/
# coalesced, per event name) but was never reachable from outside the process,
# so proving that one specific event was being DROPPED was impossible from the
# API -- which is exactly what diagnosing a frozen time reference required
# (619/513/582 all read a cached SYS_SECOND that had stopped arriving while
# 806 published it at a measured 1.000/s). New read-only route
# GET /api/bus-stats returns event_bus.stats() verbatim. No behavior changed;
# measurement only, and it is the instrument the fix in 1.29.0 is judged by.
# 1.31.0 (unification of two parallel core lines, owner order 2026-08-31
# «ادمجهم وساوي مستودع صح»): two independent efforts fixed the same root --
# the clock was owned twice (OfficialClock, plus an offset inside EventBus fed
# from the `time.utc.synced` event), so clock validity depended on consumer
# scheduling. Measured before the fix: 806 published SYS_SECOND at 1.000/s,
# the bus reported dropped=0/timeout=0/delivered=3714, and yet the pulse
# timestamp arrived with a CUMULATIVE lag (3.97s -> 60.56s -> 97.87s over
# sixteen minutes, ~0.1-0.15 s/s), so 619 computed age_s = -1.97 on a row two
# seconds old and declared fresh data stale.
#
# Taken from the repo line (branches fix/global-scheduler*, merged to master):
#   * EventBus owns no clock at all. All timestamps come from `clock.now()`.
#     `set_time_offset`/`_time_offset_s` removed; bootloader no longer wires
#     `time.utc.synced` into the bus. The event is an announcement, not a
#     source -- atom 003 corrects the clock through `clock.accept_sample`.
#   * Two bounded thread pools, with time/state/command work given a reserved
#     pool so general market pressure cannot consume all worker capacity.
#   * publish() from a non-core loop is routed back to the core loop through
#     run_coroutine_threadsafe instead of touching mailboxes cross-loop.
#   * Boot joins the unified lifecycle policy (`call_lifecycle`), so a boot
#     hang reports LIFECYCLE_TIMEOUT:<phase> like every other path.
#
# Repaired here before adoption (both measured, not opinion):
#   * `_coalesce_key` had moved into the class, breaking
#     `from core.event_bus import _coalesce_key` in transport/ownership.py --
#     the whole Event Transport layer failed at import. Restored as a
#     module-level function; the class keeps a staticmethod alias.
#   * `_worker_entry` ran `asyncio.run(result)` per delivery, i.e. a fresh
#     event loop for every event handed to every async handler. Measured
#     exposure in this tree: 26 atoms use asyncio.create_task, 5 asyncio.Lock,
#     3 asyncio.Event, 8 get_running_loop -- all created on the core loop in
#     initialize/start, so first touch from another loop raises "bound to a
#     different event loop" or hangs, and any task spawned inside a handler
#     was destroyed when asyncio.run tore its loop down. Async handlers stay
#     on the core loop; only sync handlers go to the pools.
#
# Added: a per-delivery time budget. Overruns are counted and published in
# stats()["overrun"]/["overrun_worst_s"], and stats()["pressure"] now reports
# queued depth, busy handlers and oldest_pending_age_s -- queue length alone
# cannot distinguish an item 1ms old from one 30s old, and that blindness is
# what let consumption lag masquerade as stale data.
CORE_VERSION = "1.31.0"
