from __future__ import annotations

import inspect
from pathlib import Path
import time

import pytest

import clock
from tests.test_backup_archive_contract import (
    DAY, Bus, M714, M800, M802, M803, archive_files_cfg, backup_cfg,
    cleanup_cfg, make_db, start, write_file,
)

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



@pytest.mark.asyncio
async def test_sysday_01_time_pulse_source_has_no_private_wall_clock():
    source = (ATOM_ROOT / "806_نبضة_الوقت" / "atom.py").read_text("utf-8")
    assert "time.time" not in source and "_offset_s" not in source


@pytest.mark.asyncio
async def test_sysday_02_806_restores_bucket_and_emits_current_once():
    import importlib.util, sys
    path = ATOM_ROOT / "806_نبضة_الوقت" / "atom.py"
    spec = importlib.util.spec_from_file_location("backup_sysday_806", path)
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    assert spec.loader is not None; spec.loader.exec_module(module)
    clock.reset_for_tests(); clock.configure(max_accepted_offset_s=5,
        max_sample_age_s=30, stale_after_s=900, max_slew_per_second=.05)
    assert clock.accept_sample({"median_offset_s": .1, "measured_at": time.time(),
                                "quorum": True}, writer="003")[0]
    bus = Bus(); atom = module.Atom(); await atom.initialize(bus.context(806, {}))
    event = "SYS_SECOND"; current = int(clock.now())
    await atom.restore({"last_bucket": {event: current - 4},
                        "tick_counts": {}, "max_observed_drift_s": 0.0})
    atom._running = True
    await atom._emit_tick(event); await atom._emit_tick(event)
    rows = [p for n, p in bus.events if n == event]
    assert len(rows) == 1 and rows[0]["missed_intervals"] == 4


@pytest.mark.asyncio
async def test_sysday_03_existing_714_catchup_runs_without_day(tmp_path: Path):
    source, active = tmp_path / "source.db", tmp_path / "archive.db"
    make_db(source)
    cfg = {"stores": [{"db_path": str(source), "table": "events",
                       "time_column": "occurred_at"}],
           "archive_db_path": str(active), "archive_after_days": 1,
           "batch_limit": 100}
    atom, bus = await start(M714, 714, cfg)
    await bus.publish("SYS_SECOND", {"official_time": 10 * DAY})
    assert atom._runs == 1


@pytest.mark.asyncio
async def test_sysday_04_backup_catches_up_without_day(tmp_path: Path):
    cfg = backup_cfg(tmp_path); write_file(Path(cfg["source_dirs"][0]) / "x")
    atom, bus = await start(M800, 800, cfg)
    await bus.publish("SYS_SECOND", {"official_time": 10 * DAY})
    assert atom.backup_count == 1


@pytest.mark.asyncio
async def test_sysday_05_file_archive_catches_up_without_day(tmp_path: Path):
    cfg = archive_files_cfg(tmp_path)
    write_file(Path(cfg["source_dirs"][0]) / "old.log", mtime=1.0)
    atom, bus = await start(M802, 802, cfg)
    await bus.publish("SYS_SECOND", {"official_time": 10 * DAY})
    assert atom.run_count == 1 and atom._last_success == 10 * DAY


@pytest.mark.asyncio
async def test_sysday_06_cleanup_catches_up_without_day(tmp_path: Path):
    cfg = cleanup_cfg(tmp_path)
    victim = Path(cfg["scan_dirs"][0]) / "old.tmp"; write_file(victim, mtime=1.0)
    atom, bus = await start(M803, 803, cfg)
    await bus.publish("SYS_SECOND", {"official_time": 10 * DAY})
    assert atom.run_count == 1 and not victim.exists()
