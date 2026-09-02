"""
المادة 14 / 20 / 46 — محرك الاكتشاف الحي
==========================================
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import pytest

from core.contracts.atom import AtomState
from core.event_bus import EventBus
from core.health_manager import HealthManager
from core.hot_reload_service import HotReloadService
from core.journal import Journal
from core.metrics import Metrics
from core.registry import Registry
from tests.core.conftest import write_atom


def build(atoms_root: Path):
    registry = Registry()
    bus = EventBus()
    health = HealthManager(registry, bus, Journal(), Metrics())
    service = HotReloadService(atoms_root, registry, bus, health, journal=Journal())
    return service, registry, bus, health


@pytest.mark.asyncio
async def test_new_atoms_load_in_dependency_order(atoms_root: Path) -> None:
    """ذرتان جديدتان تصلان معًا وإحداهما تعتمد على الأخرى: الترتيب
    يجب أن يتبع الاعتماديات، لا المعرّف الرقمي."""
    write_atom(atoms_root, 900)                                # يعتمد عليه
    write_atom(atoms_root, 100, dependencies=[{"id": 900}])    # يعتمد على 900

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert service.last_loaded == [900, 100], (
        f"ترتيب التحميل الحي تجاهل الاعتماديات: {service.last_loaded}"
    )
    assert registry.get(100).state == AtomState.RUNNING
    await health.stop()


@pytest.mark.asyncio
async def test_incompatible_core_version_is_not_hot_loaded(atoms_root: Path) -> None:
    """المادة 20: لا تُشغَّل حياً ذرة غير متوافقة مع إصدار النواة."""
    write_atom(atoms_root, 55, core_version=">=99.0.0")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert registry.find(55) is None, "حُمّلت ذرة غير متوافقة مع إصدار Core"
    await health.stop()


@pytest.mark.asyncio
async def test_manual_startup_mode_is_not_auto_started(atoms_root: Path) -> None:
    """startup_mode=manual يعني الاكتشاف دون تشغيل تلقائي."""
    write_atom(atoms_root, 60, startup_mode="manual")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert registry.find(60) is None, "شُغّلت ذرة معلنة startup_mode=manual تلقائيًا"
    await health.stop()


@pytest.mark.asyncio
async def test_concurrent_rescans_do_not_double_register(atoms_root: Path) -> None:
    """الفحص الدوري و POST /api/rescan قد ينطلقان معًا: القفل يمنع
    تسجيل الذرة مرتين."""
    write_atom(atoms_root, 70)
    write_atom(atoms_root, 71)

    service, registry, _, health = build(atoms_root)
    await asyncio.gather(*(service._on_rescan_requested({}) for _ in range(6)))

    assert len(registry) == 2, f"تسجيل مزدوج نتيجة فحوصات متزامنة: {len(registry)}"
    await health.stop()


@pytest.mark.asyncio
async def test_atom_moved_to_deeper_folder_keeps_working(atoms_root: Path) -> None:
    """المادة 10/45: نقل مجلد الذرة لأي عمق لا يغيّر شيئًا."""
    write_atom(atoms_root, 80, subdir="family_a/atom_80")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})
    assert registry.get(80).state == AtomState.RUNNING

    shutil.move(str(atoms_root / "family_a"), str(atoms_root / "family_b"))
    await service._on_rescan_requested({})
    assert registry.get(80).state == AtomState.RUNNING, (
        "نقل الذرة بين العائلات كسر تشغيلها"
    )
    await health.stop()


@pytest.mark.asyncio
async def test_periodic_loop_survives_scan_errors(atoms_root: Path) -> None:
    """عطل فحص عابر واحد كان يقتل الحلقة الدورية بصمت فيتوقف الاكتشاف
    الحي كله حتى إعادة التشغيل — الحلقة يجب أن تسجّل وتُكمِل."""
    service, _, _, health = build(atoms_root)
    calls: list[int] = []

    async def flaky_rescan(payload: dict) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("عطل فحص متعمَّد")

    service._on_rescan_requested = flaky_rescan  # type: ignore[method-assign]
    await service.start_periodic(0.05)
    await asyncio.sleep(0.4)
    await service.stop_periodic()

    assert len(calls) >= 2, (
        f"الحلقة الدورية ماتت بعد أول خطأ فحص (نداءات={len(calls)}) — "
        "الاكتشاف الحي توقف بصمت"
    )
    await health.stop()


@pytest.mark.asyncio
async def test_unload_order_respects_three_link_chain(atoms_root: Path) -> None:
    """سلسلة اعتماد من ثلاث حلقات (210←220←230) تُسحب كلها معًا:
    الترتيب الصحيح حتمي المعتمِد أولًا — عدّ الجيران القديم كان يخطئ
    بالسلاسل ≥3 وترتيبه غير حتمي."""
    write_atom(atoms_root, 230)
    write_atom(atoms_root, 220, dependencies=[{"id": 230}])
    write_atom(atoms_root, 210, dependencies=[{"id": 220}])

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})
    assert registry.get(210).state == AtomState.RUNNING

    for atom_id in (210, 220, 230):
        shutil.rmtree(atoms_root / f"atom_{atom_id}")
    await service._on_rescan_requested({})

    assert service.last_unloaded == [210, 220, 230], (
        f"ترتيب السحب كسر السلسلة: {service.last_unloaded} — "
        "المعتمِد يجب أن يخرج قبل معتمَده دائمًا"
    )
    await health.stop()


_SHUTDOWN_RAISER = '''
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus


class Atom(AtomBase):
    async def initialize(self, context: AtomContext) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def shutdown(self) -> None:
        raise RuntimeError("النسخة القديمة ترفض الإنهاء")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(state=HealthState.HEALTHY)
'''


@pytest.mark.asyncio
async def test_old_shutdown_failure_does_not_mark_upgrade_failed(atoms_root: Path) -> None:
    """فشل إنهاء النسخة القديمة **بعد** نجاح البديلة لا يقلب ترقية ناجحة
    إلى فاشلة — البديلة تعمل والترقية تُبلَّغ نجاحًا."""
    import yaml
    directory = atoms_root / "atom_950"
    directory.mkdir()
    manifest = {"id": 950, "name": "atom-950", "version": "1.0.0",
                "core_version": ">=1.0.0", "critical": False, "startup_mode": "auto"}
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8")
    (directory / "atom.py").write_text(_SHUTDOWN_RAISER, encoding="utf-8")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})
    assert registry.get(950).state == AtomState.RUNNING

    manifest["version"] = "2.0.0"
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8")
    await service._on_rescan_requested({})

    assert service.last_upgraded == [
        {"atom_id": 950, "from": "1.0.0", "to": "2.0.0"}
    ], "ترقية ناجحة بُلّغت فاشلة لأن النسخة القديمة رفضت أن تُنهى"
    assert registry.get(950).state == AtomState.RUNNING
    await health.stop()


_LIFECYCLE_MARKER = '''
from pathlib import Path

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

MARKS = Path(__file__).resolve().parent / "marks"


class Atom(AtomBase):
    async def initialize(self, context: AtomContext) -> None:
        raise RuntimeError("فشل متعمَّد قبل البدء")

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        (MARKS / "stopped").write_text("1", encoding="utf-8")

    async def shutdown(self) -> None:
        (MARKS / "shutdown").write_text("1", encoding="utf-8")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(state=HealthState.HEALTHY)
'''


@pytest.mark.asyncio
async def test_purge_after_init_failure_never_calls_stop(atoms_root: Path) -> None:
    """عقد دورة الحياة: ذرة فشلت في initialize لم تبدأ قط — التنظيف
    يستدعي shutdown وحدها، ولا يستدعي stop على ذرة لم تُشغَّل."""
    import yaml
    directory = atoms_root / "atom_960"
    directory.mkdir()
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump({"id": 960, "name": "atom-960", "version": "1.0.0",
                        "core_version": ">=1.0.0", "critical": False,
                        "startup_mode": "auto"}), encoding="utf-8")
    (directory / "atom.py").write_text(_LIFECYCLE_MARKER, encoding="utf-8")
    (directory / "marks").mkdir()

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert registry.find(960) is None
    assert not (directory / "marks" / "stopped").exists(), (
        "stop() استُدعيت على ذرة لم تبدأ قط — خرق عقد دورة الحياة"
    )
    assert (directory / "marks" / "shutdown").exists(), (
        "shutdown() التنظيفية لم تُستدعَ بعد فشل initialize"
    )
    await health.stop()


@pytest.mark.asyncio
async def test_rejection_warning_logged_once_across_scans(atoms_root: Path) -> None:
    """الذرة المرفوضة تُكتشف كل فحص — التحذير يُسجَّل مرة واحدة لا
    كل ٥ ثوانٍ إلى الأبد.

    إصلاح 2026-08-27: كان الاختبار يلتقط عبر caplog (معتمد على الانتشار
    إلى الجذر)، لكن core.logger.configure() يضبط propagate=False على شجرة
    quant_nq.core بعد أول مسار فشل يستدعي get_logger() — فيعمى caplog
    ويصير الاختبار رهين ترتيب التشغيل: ينجح منفرداً ويسقط داخل جولة
    كاملة. الالتقاط الآن عبر معالج خاص معلّق مباشرة على المسجّل المعنيّ،
    فلا يتأثر بسياسة الانتشار ولا بمن سبقه من الاختبارات."""
    write_atom(atoms_root, 55, core_version=">=99.0.0")

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    target_logger = logging.getLogger("quant_nq.core.hot_reload")
    capture_handler = _Capture(level=logging.WARNING)
    target_logger.addHandler(capture_handler)
    try:
        service, _, _, health = build(atoms_root)
        await service._on_rescan_requested({})
        await service._on_rescan_requested({})
        await service._on_rescan_requested({})
        await health.stop()
    finally:
        target_logger.removeHandler(capture_handler)

    hits = [r for r in captured if "رفض Hot-Load للذرة 55" in r.getMessage()]
    assert len(hits) == 1, (
        f"تحذير الرفض تكرّر {len(hits)} مرات لثلاثة فحوص — إغراق سجل بلا ذاكرة"
    )


@pytest.mark.asyncio
async def test_new_atom_depending_on_vanishing_atom_is_rejected(atoms_root: Path) -> None:
    """ذرة جديدة تعتمد على ذرة يسحبها الفحص نفسه: كانت تُقبل (الحلّ يرى
    المسحوبة حيّة بالسجل) ثم تتيتّم بعد ثوانٍ — يجب أن تُرفض صراحة."""
    write_atom(atoms_root, 300)
    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})
    assert registry.get(300).state == AtomState.RUNNING

    shutil.rmtree(atoms_root / "atom_300")
    write_atom(atoms_root, 301, dependencies=[{"id": 300}])
    await service._on_rescan_requested({})

    assert registry.find(301) is None, (
        "قُبلت ذرة جديدة تعتمد على ذرة سُحبت في نفس الفحص — يتيمة بعد ثوانٍ"
    )
    assert service.last_unloaded == [300]
    await health.stop()


@pytest.mark.asyncio
async def test_broken_manifest_does_not_stop_discovery(atoms_root: Path) -> None:
    """المادة 81: مانيفست تالف واحد لا يمنع اكتشاف البقية."""
    write_atom(atoms_root, 90)
    broken = atoms_root / "broken"
    broken.mkdir()
    (broken / "manifest.yaml").write_text("id: [هذا ليس رقمًا\n", encoding="utf-8")

    service, registry, _, health = build(atoms_root)
    await service._on_rescan_requested({})

    assert registry.find(90) is not None, "مانيفست تالف أوقف اكتشاف الذرات السليمة"
    await health.stop()
