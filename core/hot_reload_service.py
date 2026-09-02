"""
core/hot_reload_service.py — Runtime Discovery Engine: اكتشاف حي
(Hot Plug / Hot Unplug) لمجلد atoms/ أثناء تشغيل Core، بدون إعادة
تشغيله (المادة 14 و 46).

الضمانات المحفوظة هنا:
  * المادة 15 — الإزالة الصامتة تُطهّر Registry و Health Manager و
    Event Bus معًا، ولا تترك أي أثر في الذاكرة.
  * المادة 20 — لا تُحمَّل حياً أي ذرة غير متوافقة مع إصدار Core.
  * المادة 21 — فشل أي ذرة (ولو حرجة) لا يوقف الفحص ولا Core.
  * المادة 11 — ذرة تعثّرت أثناء التحميل الحي تُنظَّف وتُلغى تسجيلها
    بالكامل بدل بقائها كسجل ميت في Registry.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
from pathlib import Path
from typing import Any, Protocol

from core.contracts.atom import AtomState
from core.lifecycle import call_lifecycle

_log = logging.getLogger("quant_nq.core.hot_reload")


class RegistryProtocol(Protocol):
    def register(self, manifest: Any, instance: Any) -> Any: ...
    def unregister(self, atom_id: int) -> None: ...
    def all(self) -> list[Any]: ...
    def find(self, atom_id: int) -> Any | None: ...
    def set_state(self, atom_id: int, state: AtomState) -> None: ...


class HealthManagerProtocol(Protocol):
    def watch(self, atom_id: int) -> None: ...
    def unwatch(self, atom_id: int) -> None: ...


def _disk_fingerprint(root: Path) -> frozenset:
    """Return a content-aware, recursive fingerprint of discovered atom dirs.

    Directory mtime alone is not a reliable contract: a fast replace can keep
    the same timestamp/size on some filesystems. The worker records every file
    name/stat and hashes source files, so an immediate code deletion or a
    same-size manifest version bump cannot be skipped by the rescan gate.
    """
    rows: list[tuple[str, int, int, str | None]] = []
    try:
        manifest_paths = sorted(root.rglob("manifest.yaml"))
    except OSError:
        manifest_paths = []
    for manifest_path in manifest_paths:
        atom_dir = manifest_path.parent
        try:
            atom_files = sorted(path for path in atom_dir.iterdir() if path.is_file())
        except OSError:
            atom_files = []
        for path in atom_files:
            try:
                stat = path.stat()
                digest = None
                if path.name in {"manifest.yaml"} or path.suffix == ".py":
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                rows.append((str(path), stat.st_mtime_ns, stat.st_size, digest))
            except (OSError, ValueError):
                rows.append((str(path), -1, -1, "<unreadable>"))
        try:
            stat = atom_dir.stat()
            rows.append((str(atom_dir), stat.st_mtime_ns, -2, None))
        except OSError:
            rows.append((str(atom_dir), -1, -2, None))
    return frozenset(rows)


class EventBusProtocol(Protocol):
    def subscribe(self, event_name: str, handler, *, subscriber: str = "") -> None: ...
    def unsubscribe_all(self, subscriber: str) -> int: ...
    async def publish(self, event_name: str, payload: dict, *, publisher: str = "") -> None: ...


class HotReloadService:
    def __init__(
        self,
        atoms_root: Path,
        registry: RegistryProtocol,
        event_bus: EventBusProtocol,
        health_manager: HealthManagerProtocol,
        journal: Any = None,
        snapshot_engine: Any = None,
    ) -> None:
        self._atoms_root = atoms_root
        self._registry = registry
        self._event_bus = event_bus
        self._health_manager = health_manager
        self._journal = journal
        self._snapshot_engine = snapshot_engine
        self.rescan_count = 0
        self._periodic_task: asyncio.Task | None = None
        self.last_loaded: list[int] = []
        self.last_unloaded: list[int] = []
        self.last_upgraded: list[dict] = []
        # قفل إعادة الدخول: الفحص الدوري و POST /api/rescan يستطيعان
        # الانطلاق في نفس اللحظة. بدون هذا القفل تُسجَّل الذرة الجديدة
        # مرتين وتنفجر RegistryError، أو تُحمَّل نسختان منها في الذاكرة.
        self._scan_lock = asyncio.Lock()
        # ذاكرة تحذيرات الرفض: الذرة المرفوضة تُكتشف من جديد كل فحص (٥ ثوانٍ)،
        # وبلا ذاكرة يُغرَق السجل بنفس التحذير إلى الأبد. يُحذَف المفتاح عندما
        # تختفي الذرة من القرص أو تُحمَّل فعلًا، فيعود التحذير حيًّا إن تكرّر الرفض.
        self._warned_rejections: set[tuple[int, str]] = set()
        # بوّابة البصمة: آخر بصمة قرص مُسحت فعلًا + عدّاد الفحوص الموفَّرة (صدق
        # المراقبة: «كم مرة وجدنا القرص ساكنًا فوفّرنا المسح الكامل»).
        self._last_disk_fingerprint: frozenset | None = None
        self.skipped_scans = 0

    def register(self) -> None:
        self._event_bus.subscribe(
            "core.system.rescan_requested", self._on_rescan_requested, subscriber="core.hot_reload"
        )

    async def start_periodic(self, interval_s: float = 5.0) -> None:
        self._periodic_task = asyncio.create_task(self._periodic_loop(interval_s))

    async def stop_periodic(self) -> None:
        if self._periodic_task is not None:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            self._periodic_task = None

    async def _periodic_loop(self, interval_s: float) -> None:
        # خطأ فحص واحد (قرص، مانيفست، ناقل…) كان يقتل هذه المهمة بصمت فيتوقف
        # الاكتشاف الحي كله حتى إعادة التشغيل. الفشل يُسجَّل والحلقة تُكمِل —
        # الإلغاء وحده يُنهيها.
        while True:
            try:
                await asyncio.sleep(interval_s)
                await self._on_rescan_requested({})
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 — عطل فحص عابر لا يُميت المحرك
                _log.error("فشل فحص دوري (يُتابَع بالفحص القادم): %s", exc, exc_info=True)

    async def _on_rescan_requested(self, payload: dict) -> None:
        """نقطة الدخول الوحيدة لأي فحص حي. محمية بقفل يمنع تداخل
        الفحص الدوري مع الفحص اليدوي (POST /api/rescan)."""
        force = bool(payload.get("force")) if isinstance(payload, dict) else False
        async with self._scan_lock:
            await self._rescan_once(force=force)

    async def _rescan_once(self, force: bool = False) -> None:
        from core.dependency_resolver import find_unresolvable_dependencies, resolve
        from core.manifest_loader import scan
        from core.version_manager import find_core_incompatible_atoms

        # بوّابة البصمة: القرص لم يتغيّر منذ آخر مسح فعلي → لا شيء يُحمَّل أو
        # يُسحَب أو يُرقّى حكمًا، فنوفّر تحليل الـYAML كله. الفحص اليدوي
        # (POST /api/rescan) يمرّ دائمًا بالقوة. البصمة تُؤخذ **قبل** المسح:
        # تغيُّرٌ يقع بينهما يجعل بصمة الجولة القادمة مختلفة فيعاد المسح — لا
        # يمكن أن يضيع تغيير.
        fingerprint = await asyncio.to_thread(_disk_fingerprint, self._atoms_root)
        if not force and fingerprint == self._last_disk_fingerprint:
            self.skipped_scans += 1
            return

        # التحليل الكامل (ملفات + YAML) في خيط منفصل — الحلقة الرئيسية للسوق.
        discovery = await asyncio.to_thread(scan, self._atoms_root)
        self._last_disk_fingerprint = fingerprint
        discovered_by_id = {a.manifest.id: a for a in discovery.atoms}
        registered_ids = {r.id for r in self._registry.all()}

        newly_discovered = set(discovered_by_id) - registered_ids
        # تُحسب المسحوبات هنا (قبل حلّ الاعتماديات) لا بعد التحميل: ذرة جديدة
        # تعتمد على ذرة سيسحبها هذا الفحص نفسه كانت تُقبل ثم تتيتّم بعد ثوانٍ.
        newly_missing = registered_ids - set(discovered_by_id)
        rejected: list[tuple[int, str]] = []

        def _warn_once(atom_id: int, reason: str, fmt: str) -> None:
            if (atom_id, reason) in self._warned_rejections:
                return
            self._warned_rejections.add((atom_id, reason))
            _log.warning(fmt, atom_id, reason)

        # المادة 14 تُلزم بدعم "تعديل أو ترقية" ذرة أثناء التشغيل، لا
        # الإضافة والحذف فقط. الذرة التي رُفع إصدارها في مكانها تُعامَل
        # كسحب ثم تحميل: تُفرَّغ حالتها، تُطهَّر بالكامل، ثم يُحمَّل
        # الكود الجديد. المقارنة بالإصدار المعلن حصرًا — لا ببصمة الملف —
        # لأن المانيفست هو العقد الحاكم (المادة 18/68).
        upgrades: list[tuple[int, str, str]] = []
        for record in self._registry.all():
            found = discovered_by_id.get(record.id)
            if found is None:
                continue
            old_v, new_v = str(record.manifest.version), str(found.manifest.version)
            if old_v != new_v:
                upgrades.append((record.id, old_v, new_v))

        # المادة 21 من دستور المانيفست: startup_mode يحدّد من يُشغَّل
        # تلقائيًا. MANUAL/LAZY تُكتشف وتُترك، ولا تُقحَم في التشغيل.
        for atom_id in sorted(newly_discovered):
            mode = getattr(discovered_by_id[atom_id].manifest.startup_mode, "value", "auto")
            if mode != "auto":
                rejected.append((atom_id, f"startup_mode={mode} — لا تُشغَّل تلقائيًا"))
        newly_discovered -= {aid for aid, _ in rejected}

        # المادة 20: لا تُحمَّل حياً ذرة غير متوافقة مع إصدار Core.
        incompatible = find_core_incompatible_atoms(
            [discovered_by_id[aid].manifest for aid in newly_discovered]
        )
        for atom_id, reason in sorted(incompatible.items()):
            rejected.append((atom_id, reason))
            _warn_once(atom_id, reason, "رفض Hot-Load للذرة %s: %s")
        newly_discovered -= set(incompatible)

        # اعتماديات غير قابلة للحل مقابل الحالة الحية الفعلية للنظام — بعد
        # استبعاد ما سيُسحب في هذا الفحص نفسه (لا يُعتمد على ذرة تغادر الآن).
        live_manifests = [r.manifest for r in self._registry.all()
                          if r.id not in newly_missing]
        candidate_manifests = live_manifests + [
            discovered_by_id[aid].manifest for aid in newly_discovered
        ]
        unresolvable = find_unresolvable_dependencies(candidate_manifests)
        for atom_id in sorted(newly_discovered & set(unresolvable)):
            reason = f"اعتماديات غير محلولة {unresolvable[atom_id]}"
            rejected.append((atom_id, reason))
            _warn_once(atom_id, reason, "استبعاد الذرة الجديدة %s من Hot-Load: %s")
        newly_discovered -= set(unresolvable)

        load_order = self._load_order(
            newly_discovered, candidate_manifests, resolve
        )

        upgraded: list[dict] = []
        for atom_id, old_v, new_v in sorted(upgrades):
            old_record = self._registry.find(atom_id)
            try:
                # لا تُطهَّر موديولات النسخة القديمة قبل نجاح البديلة: التحميل
                # الجديد يكتب فوقها بنفس المفاتيح أصلًا، وبقاؤها هو ما يجعل
                # rollback يعيد النسخة القديمة ببيئتها كاملة لا كائنًا مبتورًا.
                await self._hot_unload(atom_id, preserve_state=True, finalize=False,
                                       purge_modules=False)
                try:
                    await self._hot_load(discovered_by_id[atom_id], restore_state=True)
                except BaseException:
                    if old_record is not None:
                        await self._rollback_old(old_record)
                    raise
                # الترقية نجحت هنا نهائيًا: رفضُ النسخة القديمة أن تُنهى بعدها
                # لا يقلب ترقية ناجحة إلى فاشلة — يُسجَّل تحذيرًا ويُتابَع.
                if old_record is not None:
                    try:
                        await call_lifecycle(old_record.instance.shutdown(), "shutdown")
                    except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
                        _log.warning("فشل shutdown للنسخة القديمة بعد ترقية ناجحة للذرة %s: %s",
                                     atom_id, exc)
                upgraded.append({"atom_id": atom_id, "from": old_v, "to": new_v})
                _log.info("رُقّيت الذرة %s حياً: %s ← %s", atom_id, old_v, new_v)
            except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
                if old_record is not None and self._registry.find(atom_id) is None:
                    try: await self._rollback_old(old_record)
                    except Exception as rollback_exc:
                        _log.error("فشل rollback للذرة %s: %s", atom_id, rollback_exc)
                _log.error("فشلت ترقية الذرة %s (%s ← %s): %s", atom_id, old_v, new_v, exc)

        loaded: list[int] = []
        load_failed: list[tuple[int, str]] = []
        for atom_id in load_order:
            try:
                await self._hot_load(discovered_by_id[atom_id])
                loaded.append(atom_id)
            except Exception as exc:  # noqa: BLE001 — كود ذرة خارجي
                load_failed.append((atom_id, str(exc)))
                _log.error("فشل Hot-Load للذرة %s: %s — نُظّفت بالكامل", atom_id, exc)

        # الإزالة تسير بعكس ترتيب الاعتماديات: المُعتمِد قبل المُعتمَد
        # عليه، حتى لا تُسحب ذرة من تحت ذرة ما زالت تستخدمها.
        # (newly_missing حُسبت أعلاه قبل حلّ الاعتماديات.)
        unload_order = self._unload_order(newly_missing)

        unloaded: list[int] = []
        unload_failed: list[tuple[int, str]] = []
        for atom_id in unload_order:
            try:
                await self._hot_unload(atom_id)
                if self._snapshot_engine is not None:
                    self._snapshot_engine.discard(atom_id)
                unloaded.append(atom_id)
            except Exception as exc:  # noqa: BLE001
                unload_failed.append((atom_id, str(exc)))
                _log.error("فشل Hot-Unload للذرة %s: %s", atom_id, exc)

        self.rescan_count += 1
        self.last_loaded, self.last_unloaded = loaded, unloaded
        self.last_upgraded = upgraded

        # تشذيب ذاكرة التحذيرات: ما اختفى من القرص أو صار محمَّلًا فعلًا
        # يُنسى، فيعود تحذيره حيًّا إن تكرّر رفضه مستقبلًا.
        self._warned_rejections = {
            (aid, reason) for (aid, reason) in self._warned_rejections
            if aid in discovered_by_id and self._registry.find(aid) is None
        }

        await self._event_bus.publish(
            "hot_reload.completed",
            {
                "loaded": loaded, "load_failed": load_failed,
                "unloaded": unloaded, "unload_failed": unload_failed,
                "upgraded": upgraded,
                "dependency_blocked": rejected,
                "scan_failures": [{"path": str(f.path), "error": f.error} for f in discovery.failures],
            },
            publisher="core.hot_reload",
        )
        _log.info(
            "Hot-Reload: حُمِّلت=%s أُزيلت=%s رُقّيت=%s مرفوضة=%s",
            loaded, unloaded, [u["atom_id"] for u in upgraded], rejected,
        )

    @staticmethod
    def _load_order(new_ids: set[int], candidates: list, resolve) -> list[int]:  # noqa: ANN001
        """ترتيب تحميل يحترم الاعتماديات: ذرتان جديدتان تصلان معًا
        وإحداهما تعتمد على الأخرى يجب أن تُحمّلا بالترتيب الصحيح، لا
        بترتيب المعرّف الرقمي."""
        if not new_ids:
            return []
        try:
            full_order = resolve(candidates).boot_order
        except Exception:  # noqa: BLE001 — رسم غير قابل للحل: ارجع لترتيب مستقر
            return sorted(new_ids)
        return [aid for aid in full_order if aid in new_ids]

    def _unload_order(self, missing_ids: set[int]) -> list[int]:
        """يُخرج المُعتمِدين أولًا. أي ذرة باقية تعتمد على ذرة مسحوبة
        يُنبَّه عنها صراحةً — المشغّل سحب اعتمادية من تحت ذرة حية."""
        if not missing_ids:
            return []
        depends_on: dict[int, set[int]] = {}
        for record in self._registry.all():
            depends_on[record.id] = {d.id for d in record.manifest.dependencies}

        for atom_id, deps in depends_on.items():
            if atom_id in missing_ids:
                continue
            orphaned = deps & missing_ids
            if orphaned:
                _log.warning(
                    "الذرة %s تعتمد على ذرة/ذرات تُسحب الآن %s — قد تتدهور صحتها؛ "
                    "Health Manager سيرصد ذلك (المادة 83)",
                    atom_id, sorted(orphaned),
                )

        # فرز طوبولوجي حقيقي داخل مجموعة المسحوبات: المعتمِد يخرج قبل معتمَده
        # مهما طالت السلسلة، والترتيب حتمي (كسر التعادل بالمعرّف). العدّ
        # بالجيران كان يخطئ بالسلاسل من ثلاث حلقات فأكثر وترتيبه غير حتمي
        # لاعتماده على ترتيب set الداخلي.
        remaining = set(missing_ids)
        ordered: list[int] = []
        while remaining:
            ready = sorted(
                aid for aid in remaining
                if not any(aid in depends_on.get(other, set())
                           for other in remaining if other != aid)
            )
            if not ready:
                _log.warning("حلقة اعتماديات بين المسحوبات %s — سحب بترتيب المعرّف",
                             sorted(remaining))
                ordered.extend(sorted(remaining))
                break
            ordered.extend(ready)
            remaining -= set(ready)
        return ordered

    async def _hot_load(self, discovered, restore_state: bool = False) -> None:  # noqa: ANN001
        from core.bootloader import Bootloader  # بوابة التشكيل الرسمية الموحدة
        from core.contracts.atom import AtomContext
        from core.logger import get_logger

        atom_id = discovered.manifest.id
        unavailable = [dep.id for dep in discovered.manifest.dependencies
                       if self._registry.find(dep.id) is None
                       or self._registry.find(dep.id).state != AtomState.RUNNING]
        if unavailable:
            raise RuntimeError(f"dependencies not running: {unavailable}")
        # المادة 63/64: الاعتماد على بوابة التشكيل الرسمية بدلاً من كتابة
        # كود استيراد موازٍ داخل هذه الخدمة.
        instance = Bootloader.instantiate(discovered)
        self._registry.register(discovered.manifest, instance)
        self._registry.set_state(atom_id, AtomState.REGISTERED)

        context = AtomContext(
            atom_id=atom_id,
            config=discovered.manifest.config,
            logger=get_logger(atom_id),
            publish=self._make_bound_publish(atom_id),
            subscribe=self._make_bound_subscribe(atom_id),
            subscribe_all=self._make_bound_subscribe_all(atom_id),
        )

        started_attempted = False
        try:
            self._registry.set_state(atom_id, AtomState.INITIALIZING)
            await call_lifecycle(instance.initialize(context), "initialize")
            self._registry.set_state(atom_id, AtomState.INITIALIZED)
            if restore_state and self._snapshot_engine is not None:
                await self._snapshot_engine.restore_one(atom_id)
            self._registry.set_state(atom_id, AtomState.STARTING)
            started_attempted = True
            await call_lifecycle(instance.start(), "start")
        except BaseException:
            # المادة 11 + 15 + 86: ذرة تعثّرت أثناء التحميل الحي يجب ألا
            # تبقى سجلًا ميتًا في Registry ولا اشتراكًا معلّقًا في الناقل
            # ولا موردًا مفتوحًا. تنظيف كامل، ثم يُعاد رفع الاستثناء
            # ليسجّله المستدعي.
            await self._purge_failed_load(atom_id, instance, started=started_attempted)
            raise

        self._registry.set_state(atom_id, AtomState.RUNNING)
        self._health_manager.watch(atom_id)
        if self._journal is not None:
            self._journal.record(atom_id, "hot_loaded")
        await self._event_bus.publish(
            "core.atom.started", {"atom_id": atom_id}, publisher="core.hot_reload"
        )

    async def _purge_failed_load(self, atom_id: int, instance,
                                 started: bool = True) -> None:  # noqa: ANN001
        """تنظيف بأفضل جهد لذرة فشل تحميلها الحي. لا يُسمح لأي استثناء
        بالخروج من هنا — وإلا حجب سبب الفشل الأصلي."""
        # عقد دورة الحياة: stop() لا تُستدعى على ذرة لم تبلغ start() إطلاقًا —
        # الفشل قبل البدء يُنهى بـ shutdown() وحدها.
        phases = ((("stop", instance.stop),) if started else ()) + \
                 (("shutdown", instance.shutdown),)
        for phase, fn in phases:
            try:
                await call_lifecycle(fn(), phase)
            except Exception as exc:  # noqa: BLE001
                _log.warning("فشل %s تنظيفي للذرة %s: %s", phase, atom_id, exc)
        try:
            self._health_manager.unwatch(atom_id)
            self._event_bus.unsubscribe_all(str(atom_id))
            self._registry.unregister(atom_id)
        except Exception as exc:  # noqa: BLE001
            _log.error("فشل تطهير سجلات الذرة %s بعد فشل التحميل: %s", atom_id, exc)
        # موديولات المحاولة الفاشلة تُطهَّر كذلك — الاستيراد التالي يعيد القراءة
        # من القرص على كل حال، لكن لا تُترك جثث تتراكم في sys.modules مع كل فشل.
        try:
            from core.bootloader import Bootloader
            _prefix = Bootloader.atom_module_prefix(atom_id)
            for _key in [k for k in sys.modules if k.startswith(_prefix)]:
                del sys.modules[_key]
        except Exception as exc:  # noqa: BLE001
            _log.error("فشل تطهير موديولات الذرة %s بعد فشل التحميل: %s", atom_id, exc)
        if self._journal is not None:
            self._journal.record(atom_id, "hot_load_failed")

    async def _rollback_old(self, record) -> None:  # noqa: ANN001
        """Reattach the stopped old instance when a replacement cannot start."""
        from core.contracts.atom import AtomContext
        from core.logger import get_logger
        atom_id = record.id
        if self._registry.find(atom_id) is not None:
            return
        self._registry.register(record.manifest, record.instance)
        context = AtomContext(atom_id=atom_id, config=record.manifest.config,
            logger=get_logger(atom_id), publish=self._make_bound_publish(atom_id),
            subscribe=self._make_bound_subscribe(atom_id),
            subscribe_all=self._make_bound_subscribe_all(atom_id))
        try:
            self._registry.set_state(atom_id, AtomState.INITIALIZING)
            await call_lifecycle(record.instance.initialize(context), "initialize")
            self._registry.set_state(atom_id, AtomState.INITIALIZED)
            if self._snapshot_engine is not None:
                await self._snapshot_engine.restore_one(atom_id)
            self._registry.set_state(atom_id, AtomState.STARTING)
            await call_lifecycle(record.instance.start(), "start")
            self._registry.set_state(atom_id, AtomState.RUNNING)
            self._health_manager.watch(atom_id)
            if self._journal is not None: self._journal.record(atom_id, "hot_upgrade_rolled_back")
        except BaseException:
            await self._purge_failed_load(atom_id, record.instance)
            raise

    async def _hot_unload(self, atom_id: int, preserve_state: bool = False,
                          finalize: bool = True, purge_modules: bool = True) -> None:
        record = self._registry.find(atom_id)
        if record is None:
            return
        self._health_manager.unwatch(atom_id)
        # المادة 15: التطهير مضمون حتى لو رمت الذرة استثناءً في stop()
        # أو shutdown() — لا يجوز أن يترك سلوك ذرة سيئة أثرًا في Registry
        # أو في الناقل.
        lifecycle_error: Exception | None = None
        try:
            try:
                await call_lifecycle(record.instance.stop(), "stop")
                self._registry.set_state(atom_id, AtomState.STOPPED)
                if preserve_state and self._snapshot_engine is not None:
                    await self._snapshot_engine.snapshot_one(atom_id)
            finally:
                if finalize:
                    await call_lifecycle(record.instance.shutdown(), "shutdown")
        except Exception as exc:  # noqa: BLE001
            lifecycle_error = exc
            _log.error("خطأ أثناء إيقاف/لقط/إنهاء الذرة %s عند السحب الحي: %s", atom_id, exc)
        finally:
            # خطوات التطهير معزولة عن بعضها: فشل واحدة لا يجوز أن يترك البقية
            # نصف منفَّذة بلا حدث ولا سجل (كان unregister الفاشل يقطع تنظيف
            # الناقل والموديولات ويمنع نشر core.atom.unloaded كليًا).
            removed_subs = 0
            try:
                self._registry.unregister(atom_id)
            except Exception as exc:  # noqa: BLE001
                _log.error("فشل إلغاء تسجيل الذرة %s عند السحب: %s", atom_id, exc)
            try:
                removed_subs = self._event_bus.unsubscribe_all(str(atom_id))
            except Exception as exc:  # noqa: BLE001
                _log.error("فشل تنظيف اشتراكات الذرة %s عند السحب: %s", atom_id, exc)
            if purge_modules:
                # ورقة ٠٦-ب (المادة 86): تطهير موديولات الذرة من ذاكرة المفسّر عند
                # السحب — وإلا تتراكم النسخ القديمة، وقد تتعايش نسختان بصمت بعد الترقية.
                # (مسار الترقية يمرّر purge_modules=False حتى يبقى rollback ممكنًا
                # ببيئة النسخة القديمة كاملة — التحميل الجديد يكتب فوقها بنفس المفاتيح.)
                try:
                    from core.bootloader import Bootloader
                    _prefix = Bootloader.atom_module_prefix(atom_id)
                    for _key in [k for k in sys.modules if k.startswith(_prefix)]:
                        del sys.modules[_key]
                except Exception as exc:  # noqa: BLE001
                    _log.error("فشل تطهير موديولات الذرة %s عند السحب: %s", atom_id, exc)
        if self._journal is not None:
            self._journal.record(atom_id, "hot_unloaded", {"subscriptions_cleared": removed_subs})
        await self._event_bus.publish("core.atom.unloaded", {"atom_id": atom_id}, publisher="core.hot_reload")
        if lifecycle_error is not None and preserve_state:
            raise lifecycle_error

    def _make_bound_publish(self, atom_id: int):
        async def _publish(name: str, payload: dict | None = None) -> None:
            await self._event_bus.publish(name, payload, publisher=str(atom_id))
        return _publish

    def _make_bound_subscribe(self, atom_id: int):
        def _subscribe(name: str, handler) -> None:
            self._event_bus.subscribe(name, handler, subscriber=str(atom_id))
        return _subscribe

    def _make_bound_subscribe_all(self, atom_id: int):
        def _subscribe_all(handler) -> None:
            self._event_bus.subscribe_all(handler, subscriber=str(atom_id))
        return _subscribe_all