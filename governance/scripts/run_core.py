#!/usr/bin/env python3
"""
governance/scripts/run_core.py
=====================
نقطة التشغيل الفعلية لـ Core V1.0: تحميل الإعداد → تهيئة الخدمات
المشتركة → Bootloader.boot() → (اختياري) REST/WebSocket/Dashboard →
انتظار إشارة إيقاف → إيقاف نظيف للذرات بترتيب عكسي عن الإقلاع.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from core import logger as core_logger  # noqa: E402
from core.bootloader import BootReport, Bootloader  # noqa: E402
from core.config import load_core_config  # noqa: E402
from core.contracts.atom import AtomState  # noqa: E402
from transport.owned_event_bus import OwnedEventBus  # noqa: E402
from core.health_manager import HealthManager  # noqa: E402
from core.hot_reload_service import HotReloadService  # noqa: E402
from core.journal import Journal  # noqa: E402
from core.lifecycle import call_lifecycle  # noqa: E402
from core.metrics import Metrics  # noqa: E402
from core.registry import Registry  # noqa: E402
from core.snapshot_engine import SnapshotEngine  # noqa: E402
from core.dependency_resolver import resolve  # noqa: E402


async def run(enable_api: bool | None, demo_seconds: float | None = None) -> int:
    configured_path = os.environ.get("QUANT_CORE_CONFIG")
    config_path = Path(configured_path) if configured_path else PROJECT_ROOT / "config" / "core.yaml"
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    core_config = load_core_config(config_path)
    core_logger.configure(
        level=getattr(logging, str(core_config.get("log_level", "INFO")).upper(), logging.INFO),
        json_output=bool(core_config.get("log_json", True)),
    )
    log = logging.getLogger("quant_nq.core.bootstrap")

    atoms_root = PROJECT_ROOT / core_config.get("atoms_root", "atoms")
    # A unified checkout runs Forex and Crypto with separate config files while
    # sharing the same frozen runtime code. The config is selected by the
    # wrapper through QUANT_CORE_CONFIG; default remains config/core.yaml.
    registry = Registry()
    event_bus_cfg = core_config.get("event_bus") or {}
    # The production runner uses the first executable slice of Event Ownership:
    # one immutable payload is shared across read-only consumers instead of being
    # serialized once per subscriber.  Set payload_mode: isolated for a
    # compatibility rollback/debug run.
    payload_mode = str(event_bus_cfg.get("payload_mode", "shared_readonly"))
    worker_count = int(event_bus_cfg.get("worker_count", 4))
    mailbox_max_events = int(event_bus_cfg.get("mailbox_max_events", 1024))
    partitioned_ownership = bool(event_bus_cfg.get("partitioned_ownership", True))
    event_bus = OwnedEventBus(
        payload_mode=payload_mode,
        worker_count=worker_count,
        partitioned_ownership=partitioned_ownership,
        mailbox_max_events=mailbox_max_events,
    )
    # ورقة ٠٧ (فتحة V2.0): توريث الأثر صار **جوّا الناقل** نفسه
    # (event_bus.publish — ورقة ٠٤: trace_id + event_id + parent_event_id).
    # لا لزقة خارجية بعد اليوم؛ الوسيط trace_middleware.py أُبطِل (جذع مهجور لا يُستدعى). المُشغّل صار
    # «تشغيل + توصيل» فقط، بلا حقن منطق نواة.
    metrics = Metrics()
    journal_path = (core_config.get("journal") or {}).get("path")
    journal = Journal(path=(PROJECT_ROOT / journal_path) if journal_path else None)
    health_manager = HealthManager(registry, event_bus, journal, metrics)
    snapshot_path = core_config.get("snapshot_root", "var/snapshots")
    snapshot_engine = SnapshotEngine(registry, PROJECT_ROOT / snapshot_path)

    # الأسرار تُهيّأ **قبل** الإقلاع: أول ما تفعله الذرة في
    # initialize() قد يكون طلب سرّها، فلا يجوز أن تجده فارغاً.
    _init_secret_provider(core_config, log)

    restored_at_boot: list[int] = []
    async def _restore_before_start(atom_id: int) -> None:
        if await snapshot_engine.restore_one(atom_id):
            restored_at_boot.append(atom_id)
    bootloader = Bootloader(
        atoms_root, registry, event_bus, journal, metrics, health_manager=health_manager,
        restore_hook=_restore_before_start,
    )

    # Core/Atom boundary: load the non-execution auto set first. The external
    # policy gate runs only after Bootloader has been reached; execution atoms
    # are admitted in a second generic Bootloader phase without changing their
    # manifests or startup_mode.
    from build_registry import BuildRegistry
    build_snapshot = BuildRegistry(PROJECT_ROOT).refresh()
    domain = str(os.environ.get("QUANT_CORE_DOMAIN", "forex")).strip().lower()
    domain_records = build_snapshot.crypto_all if domain == "crypto" else build_snapshot.forex_all
    auto_ids = {
        record.atom_id for record in domain_records
        if record.atom_id is not None and record.startup_mode == "auto"
    }
    scope = "crypto" if domain == "crypto" else "forex"
    domain_records = tuple(record for record in domain_records if record.atom_id is not None)
    record_by_id = {record.atom_id: record for record in domain_records}
    execution_ids = {
        record.atom_id for record in build_snapshot.execution_targets
        if record.atom_id is not None and record.scope == scope and record.startup_mode == "auto"
    }
    # Defer the reverse dependency closure as well. A non-execution atom that
    # depends on a deferred execution participant must not start half-built.
    changed = True
    while changed:
        changed = False
        for record in domain_records:
            if record.atom_id in execution_ids or record.startup_mode != "auto":
                continue
            if any(dep_id in execution_ids for dep_id in record.dependencies):
                execution_ids.add(record.atom_id)
                changed = True
    non_execution_ids = auto_ids - execution_ids

    log.info("بدء مرحلة Core/Atom غير التنفيذية من %s", atoms_root)
    base_report = await bootloader.boot(include_ids=non_execution_ids)
    _log_report(log, base_report, prefix="انتهت مرحلة Core/Atom")

    execution_report = None
    safety = _verify_execution_safety_at_startup()
    if execution_ids and safety == 0:
        log.info("بوابة execution نجحت بعد Core Boot؛ بدء أهداف التنفيذ المكتشفة")
        execution_report = await bootloader.boot(include_ids=execution_ids)
        _log_report(log, execution_report, prefix="انتهت مرحلة execution")
    elif execution_ids:
        log.error("execution محجوب بعد إقلاع Core؛ لن تبدأ أهداف التنفيذ")

    report = _merge_boot_reports(base_report, execution_report)
    _log_report(log, report)
    if not report.success:
        log.critical(
            "فشلت ذرة/ذرات حرجة أثناء إقلاع Atom: %s — Core يستمر بالعمل.",
            report.abort_reason,
        )

    if restored_at_boot:
        log.info("استُعيدت الحالة قبل start: %s", restored_at_boot)

    discovery_cfg = core_config.get("discovery") or {}
    rescan_interval_s = float(discovery_cfg.get("rescan_interval_s", 5.0))
    hot_reload = HotReloadService(atoms_root, registry, event_bus, health_manager,
                                  journal=journal, snapshot_engine=snapshot_engine)
    hot_reload.register()
    await hot_reload.start_periodic(interval_s=rescan_interval_s)
    log.info(
        "Runtime Discovery Engine مُفعَّلة — فحص تلقائي كل %.1f ثانية، "
        "+ POST /api/rescan لتفعيل فوري يدوي عند الحاجة",
        rescan_interval_s,
    )

    latest_report_box: dict[str, BootReport] = {"report": report}

    api_cfg = core_config.get("api") or {}
    use_api = enable_api if enable_api is not None else bool(api_cfg.get("enable_api", True))
    server = None
    server_task = None
    if use_api:
        server, server_task = _start_api(
            registry, event_bus, metrics, journal, latest_report_box, api_cfg, log,
            health_manager=health_manager,
            control_event_publisher=_build_control_event_publisher(event_bus, log),
        )
        await asyncio.sleep(0.2)
        if server_task.done():
            server, server_task = None, None

    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        log.info("إشارة إيقاف مستلمة")
        stop_event.set()

    loop = asyncio.get_running_loop()
    installed_signals: list[int] = []
    restore_handlers: dict = {}
    import signal
    _SIGNALS = tuple(s for s in (getattr(signal, name, None)
                                 for name in ("SIGINT", "SIGTERM", "SIGBREAK")) if s is not None)
    try:
        for sig in _SIGNALS:
            loop.add_signal_handler(sig, _request_stop)
            installed_signals.append(sig)
    except (NotImplementedError, ImportError, RuntimeError):
        # ويندوز: ProactorEventLoop يرفض add_signal_handler لكل الإشارات، فكانت
        # stop_event لا تُضبط أبدًا، و`await stop_event.wait()` لا يرجع، فلا
        # يُنفَّذ snapshot_all ولا مرّة (var\snapshots = صفر ملفّ). المسار
        # البديل يبلغ **نفس** stop_event عبر signal.signal — فيبقى snapshot_all
        # هو الطريق الوحيد للحفظ، ولا يُضاف طريق ثانٍ.
        for sig in installed_signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError):
                pass
        installed_signals.clear()

        def _request_stop_threadsafe(_signum: int, _frame: object) -> None:
            loop.call_soon_threadsafe(_request_stop)

        for sig in _SIGNALS:
            try:
                restore_handlers[sig] = signal.signal(sig, _request_stop_threadsafe)
            except (ValueError, OSError, RuntimeError):
                continue
        log.info("مقابض الإيقاف عبر signal.signal (مسار ويندوز): %s",
                 [getattr(s, "name", s) for s in restore_handlers])

    # مقبض signal.signal لا يُنفَّذ إلا حين تصحو الحلقة، وProactor قد ينام
    # طويلًا على انتظار خالص. نبضة قصيرة تضمن أن الإشارة تُنفَّذ فورًا.
    async def _signal_pump() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(0.2)

    pump = asyncio.create_task(_signal_pump()) if restore_handlers else None

    # finding 06: لقطة دوريّة كخطّ ثانٍ — لو انقطع التيار أو قُتلت العمليّة
    # فجأةً، تبقى آخر لقطة دوريّة (لا تعتمد على الإيقاف النظيف وحده).
    _PERIODIC_SNAPSHOT_INTERVAL_S = 60.0
    async def _periodic_snapshot() -> None:
        if snapshot_engine is None:
            return
        while not stop_event.is_set():
            await asyncio.sleep(_PERIODIC_SNAPSHOT_INTERVAL_S)
            if stop_event.is_set():
                break
            try:
                report = await snapshot_engine.snapshot_all()
                if report.captured:
                    log.debug("لقطة دوريّة: %s ذرّة", len(report.captured))
            except Exception as exc:  # noqa: BLE001
                log.warning("فشل اللقطة الدوريّة: %s", exc)

    periodic_snap = asyncio.create_task(_periodic_snapshot())

    # Item 64: a bounded foreground run. The flag existed, the canonical script
    # had lost it, and the only two tests that run the core as a real process
    # were killed by `unrecognized arguments: --demo-seconds` -- which read from
    # the outside as "the core did not boot". It stops through the SAME
    # stop_event, so the clean-shutdown snapshot path is not bypassed.
    if demo_seconds is not None and demo_seconds > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=demo_seconds)
        except asyncio.TimeoutError:
            log.info("انتهت المدّة المحدودة (%.0fs) — إيقاف نظيف", demo_seconds)
            stop_event.set()
    else:
        await stop_event.wait()
    if pump is not None:
        pump.cancel()
    periodic_snap.cancel()

    log.info("إيقاف نظيف لكل الذرات (بترتيب عكسي عن الإقلاع والاعتماديات حياً)...")
    # يجب أن يتوقف الاكتشاف الحي أولًا: لقطة تُلتقط بينما يُحمَّل محرك
    # الاكتشاف ذرة جديدة تعني لقطة لحالة متغيّرة تحت أقدامنا.
    await hot_reload.stop_periodic()
    # Stop first, then snapshot quiescent state, then release resources.
    await _shutdown(registry, health_manager, log, snapshot_engine)
    # Finish and close the ownership lanes after atom shutdown. This prevents
    # pending worker tasks from being abandoned at asyncio.run() teardown.
    if hasattr(event_bus, "aclose"):
        await event_bus.aclose()

    for sig in installed_signals:
        try:
            loop.remove_signal_handler(sig)
        except (NotImplementedError, RuntimeError):
            pass
    for sig, previous in restore_handlers.items():
        try:
            signal.signal(sig, previous)
        except (ValueError, OSError, RuntimeError):
            pass

    if server is not None:
        server.should_exit = True
        await server_task

    log.info("تم إيقاف Core بنجاح")
    return 0


def _log_report(log: logging.Logger, report: BootReport, *, prefix: str = "انتهى الإقلاع") -> None:
    log.info(
        "%s: نجاح=%s مدة=%.2fث بدأت=%s فشلت=%s استُبعدت=%s",
        prefix, report.success, report.duration_s, report.booted, report.failed, report.excluded,
    )
    for f in report.scan_failures:
        log.warning("manifest مرفوض: %s — %s", f.path, f.error)


def _merge_boot_reports(first: BootReport, second: BootReport | None) -> BootReport:
    """Combine phased atom reports without turning deferred atoms into failures."""
    reports = [first] if second is None else [first, second]

    def unique(selector):
        return list(dict.fromkeys(item for report in reports for item in selector(report)))

    abort_reasons = [report.abort_reason for report in reports if report.abort_reason]
    return BootReport(
        started_at=min(report.started_at for report in reports),
        finished_at=max(report.finished_at for report in reports),
        success=all(report.success for report in reports),
        booted=unique(lambda report: report.booted),
        failed=unique(lambda report: report.failed),
        excluded=unique(lambda report: report.excluded),
        scan_failures=[failure for report in reports for failure in report.scan_failures],
        abort_reason="؛ ".join(abort_reasons) or None,
    )


async def _serve_with_error_handling(server, host: str, port: int, log: logging.Logger) -> None:
    try:
        await server.serve()
    except SystemExit as exc:
        log.error(
            "فشل بدء خادم API/WebSocket للنواة على %s:%s (كود %s) — على الأرجح "
            "تضارب منفذ أو نقص صلاحيات. Core يستمر بدون واجهة API.",
            host, port, exc.code,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("خطأ غير متوقَّع في خادم API/Dashboard: %s — Core يستمر بدونها.", exc)


def _init_secret_provider(core_config: dict, log) -> None:  # noqa: ANN001
    """يهيّئ مزوّد الأسرار قبل إقلاع الذرات.

    خارج النواة تماماً: `core/` لا يعرف أن هذه الحزمة موجودة. حذف مجلد
    `security/` يجعل هذه الدالة تتجاوز نفسها بصمت وتعمل النواة كما هي
    (المادة 1/41).
    """
    cfg = core_config.get("secrets") or {}
    if not cfg.get("enabled", True):
        return
    try:
        from security import (
            ChainSecretProvider, EnvSecretProvider, FileSecretProvider,
            set_secret_provider,
        )
    except ImportError:
        log.info("حزمة security غير موجودة — يعمل النظام بلا مخزن أسرار")
        return

    vault_path = Path(cfg.get("vault_path", "runtime/secrets.enc"))
    provider = ChainSecretProvider(
        FileSecretProvider(
            vault_path,
            dpapi_blob=cfg.get("dpapi_blob"),
            allow_prompt=bool(cfg.get("allow_prompt", True)),
        ),
        EnvSecretProvider(prefix=cfg.get("env_prefix", "QUANT_SECRET_")),
    )
    set_secret_provider(provider)
    log.info("مزوّد الأسرار: %s", provider.health())


def _build_control_event_publisher(event_bus, log):  # noqa: ANN001, ANN201
    """Create a domain adapter outside Core; absent adapter means closed ingress."""
    domain = str(os.environ.get("QUANT_CORE_DOMAIN", "")).strip().lower()
    if not domain:
        return None
    try:
        from governance.control_adapter import build_control_event_publisher
        return build_control_event_publisher(domain, event_bus)
    except Exception as exc:  # noqa: BLE001
        log.error("تعذّر تحميل محوّل التحكم للنطاق %s: %s", domain, exc)
        return None


def _start_api(registry, event_bus, metrics, journal, latest_report_box, api_cfg, log,
               health_manager=None, control_event_publisher=None):  # noqa: ANN001, ANN201
    import uvicorn
    from core.api.app import create_app

    local_mode = os.environ.get("QUANT_LOCAL_MODE", "").strip() == "1"
    host, port = ("127.0.0.1" if local_mode else api_cfg.get("host", "127.0.0.1")), int(api_cfg.get("port", 8000))
    api_key = (os.environ.get("QUANT_CORE_API_KEY")
               or os.environ.get("QUANT_GOV_API_KEY") or api_cfg.get("api_key"))
    if local_mode:
        api_key = None
    if api_key is None and host != "127.0.0.1":
        log.warning(
            "⚠️ api_key غير مُعدَّة و host=%s (ليس 127.0.0.1) — المادة 28 (Secure by Default) "
            "توصي بشدة بتعيين api_key بـcore.yaml لأي ربط متاح خارج الجهاز المحلي.", host,
        )
    app = create_app(
        registry, event_bus, metrics, journal,
        get_boot_report=lambda: latest_report_box["report"],
        api_key=api_key,
        health_manager=health_manager,
        control_event_publisher=control_event_publisher,
    )
    # ضغط إطارات WebSocket (permessage-deflate) مطفأ عمدًا: القناة محلية
    # (اللوحة على نفس الجهاز) والضغط كان يُنفَّذ على الحلقة الرئيسية لكل حدث
    # فيسرق ~5% من وقتها تحت تدفّق سبعة رموز (مقيس py-spy‏ 2026-08-19).
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            ws_per_message_deflate=False)
    server = uvicorn.Server(config)
    log.info("محاولة بدء API/WebSocket للنواة على http://%s:%s", host, port)
    task = asyncio.create_task(_serve_with_error_handling(server, host, port, log))
    return server, task


async def _shutdown(registry: Registry, health_manager: HealthManager, log: logging.Logger,
                    snapshot_engine: SnapshotEngine | None = None) -> None:
    """⚠️ تطبيق المادة 11 و 16: الإيقاف المبني على السجل الديناميكي (يشمل الذرات المحملة حياً) بالترتيب الهرمي"""
    await health_manager.stop()

    # سحب جميع الذرات المعترف بها حالياً وحل اعتمادياتها لتحديد ترتيب التوقف الدقيق
    active_manifests = [r.manifest for r in registry.all()]
    try:
        graph = resolve(active_manifests)
        shutdown_order = list(reversed(graph.boot_order))
    except Exception as exc:
        log.warning("تعذّر حل الاعتماديات لتحديد ترتيب الإيقاف الدقيق (%s)، سيتم الإيقاف كإجراء طوارئ.", exc)
        shutdown_order = [r.id for r in registry.all()]

    for atom_id in shutdown_order:
        record = registry.find(atom_id)
        if record is None or record.state != AtomState.RUNNING:
            continue
        try:
            await call_lifecycle(record.instance.stop(), "stop")
            registry.set_state(atom_id, AtomState.STOPPED)
        except Exception as exc:  # noqa: BLE001
            log.error("فشل إيقاف الذرة %s بأمان: %s", atom_id, exc)

    if snapshot_engine is not None:
        snap_report = await snapshot_engine.snapshot_all()
        if snap_report.captured: log.info("التُقطت حالة ساكنة للذرات: %s", snap_report.captured)
        if snap_report.failed: log.warning("فشل التقاط حالة الذرات: %s", snap_report.failed)

    # مرحلة التنظيف النهائي (تتبع نفس مسار الترتيب العكسي للاعتماديات)
    for atom_id in shutdown_order:
        record = registry.find(atom_id)
        if record is None:
            continue
        try:
            await call_lifecycle(record.instance.shutdown(), "shutdown")
        except Exception as exc:  # noqa: BLE001
            log.error("فشل shutdown نهائي للذرة %s: %s", record.id, exc)


def _verify_execution_safety_at_startup() -> int:
    """Run external execution policy only for discovered auto targets.

    BuildRegistry owns recursive discovery. The runner never guesses a path or
    a build count, and a scope with no auto execution targets is reported
    explicitly as outside this policy surface.
    """
    from build_registry import BuildRegistry

    domain = str(os.environ.get("QUANT_CORE_DOMAIN", "forex")).strip().lower()
    scope = "crypto" if domain == "crypto" else "forex"
    snapshot = BuildRegistry(PROJECT_ROOT).refresh()
    execution_targets = tuple(
        record for record in snapshot.execution_targets
        if record.scope == scope and record.startup_mode == "auto"
    )
    if not execution_targets:
        print(f"🛡️ فحص سلامة التنفيذ: تُخُطّي — لا أهداف تنفيذ auto في نطاق {scope}.")
        return 0
    # سياسة تنفيذ الكريبتو — اعتمدها المالك 2026-08-28 (جلسة «أعلى جهوزية»):
    # نفس بوّابة سلامة التنفيذ الحاكمة للفوركس (check_execution_safety) تحكم
    # نطاق الكريبتو — لا معاملة خاصة ولا تخفيف؛ التنفيذ الحي بلا مفاتيح أصلًا
    # (الجسر الصوري 2626 هو مسار التجفيف الجاف).
    try:
        from governance.checks.check_execution_safety import inspect
        ok, problems = inspect(PROJECT_ROOT)
    except Exception as exc:  # noqa: BLE001
        print(f"⛔ رفض التشغيل: تعذّر فحص سلامة التنفيذ ({type(exc).__name__})")
        return 4
    if ok:
        print(f"🛡️ فحص سلامة التنفيذ: {len(execution_targets)} هدفًا مكتشفًا عبر Registry.")
        return 0
    print("⛔ رفض التشغيل: بوابة سلامة التنفيذ مغلقة.")
    for problem in problems:
        print("   " + problem)
    return 4

def _verify_seal_at_startup() -> int:
    """سلامة الختم شرط تشغيلي لا فحص يدوي (سدّ فجوة معروفة — 2026-08-07):
    - مختومة وسليمة → تقلع.
    - مختومة ومُنتهَكة → **ترفض الإقلاع** (نفس منطق freeze_core، والرسالة تشرح المخرج:
      إصلاح الملفات أو reseal متعمَّد بعد رفع الإصدار).
    - غير مختومة (المالك فكّ الختم عمدًا) → تحذير صريح وتقلع — فكّ الختم قراره لا قرارنا.
    المشغّل خارج الختم (ورقة ٠٧/١٥) — النواة لا تحرس نفسها."""
    try:
        from governance.scripts.freeze_core import LOCK_FILE, verify
    except ImportError:
        from governance.scripts.freeze_core import LOCK_FILE, verify
    if not LOCK_FILE.exists():
        print("⚠️ تحذير: النواة غير مختومة (لا CORE.lock) — الإقلاع مستمر، والختم قرار المالك.")
        return 0
    result = verify(quiet=True)
    if result == 0:
        print("🔒 فحص الختم عند الإقلاع: النواة سليمة ومطابقة.")
        return 0
    print("⛔ رفض الإقلاع: خرق ختم النواة — التفاصيل الكاملة:")
    verify(quiet=False)
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description="تشغيل QUANT_NQ Core V1.0")
    parser.add_argument("--no-api", action="store_true", help="تعطيل REST/WebSocket/Dashboard")
    parser.add_argument("--skip-seal-check", action="store_true",
                        help="تجاوز فحص الختم عند الإقلاع (طوارئ فقط — بقرار المالك)")
    parser.add_argument("--demo-seconds", type=float, default=None,
                        help="تشغيل أماميّ محدود بالمدّة ثمّ إيقاف نظيف (اختبار)")
    args = parser.parse_args()
    if not args.skip_seal_check:
        seal = _verify_seal_at_startup()
        if seal != 0:
            return seal
    try:
        return asyncio.run(run(enable_api=(False if args.no_api else None),
                               demo_seconds=args.demo_seconds))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
