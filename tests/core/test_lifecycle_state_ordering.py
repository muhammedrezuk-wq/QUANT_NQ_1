from __future__ import annotations

import pytest

from core.bootloader import Bootloader
from core.event_bus import EventBus
from core.hot_reload_service import HotReloadService
from core.journal import Journal
from core.metrics import Metrics
from core.registry import Registry
from tests.core.conftest import write_atom


@pytest.mark.asyncio
async def test_restore_hook_runs_after_initialize_before_start(atoms_root) -> None:
    write_atom(atoms_root, 21)
    registry = Registry()

    async def restore(atom_id: int) -> None:
        registry.get(atom_id).instance.events.append("restore")

    loader = Bootloader(atoms_root, registry, EventBus(), Journal(), Metrics(),
                        restore_hook=restore)
    report = await loader.boot()
    assert report.booted == [21]
    assert registry.get(21).instance.events == ["initialize", "restore", "start"]


@pytest.mark.asyncio
async def test_failed_dependency_cannot_start(atoms_root) -> None:
    write_atom(atoms_root, 31, fail_on="start")
    write_atom(atoms_root, 32, dependencies=[{"id": 31}])
    registry = Registry()
    report = await Bootloader(
        atoms_root, registry, EventBus(), Journal(), Metrics()).boot()
    assert set(report.failed) == {31, 32}
    assert registry.get(32).instance.events == []


@pytest.mark.asyncio
async def test_hot_upgrade_unload_order_is_stop_snapshot_shutdown(atoms_root) -> None:
    write_atom(atoms_root, 41)
    registry = Registry(); bus = EventBus(); journal = Journal(); metrics = Metrics()
    await Bootloader(atoms_root, registry, bus, journal, metrics).boot()

    class Health:
        def unwatch(self, atom_id: int) -> None: pass
        def watch(self, atom_id: int) -> None: pass

    class Snapshots:
        async def snapshot_one(self, atom_id: int) -> bool:
            registry.get(atom_id).instance.events.append("snapshot")
            return True

    service = HotReloadService(atoms_root, registry, bus, Health(), journal=journal,
                               snapshot_engine=Snapshots())
    instance = registry.get(41).instance
    await service._hot_unload(41, preserve_state=True)
    assert instance.events == ["initialize", "start", "stop", "snapshot", "shutdown"]
