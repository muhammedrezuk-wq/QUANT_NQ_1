from __future__ import annotations

import asyncio
import gzip
import importlib.util
import inspect
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import time
from typing import Any

import pytest

from core.contracts.atom import AtomContext, HealthState

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

DAY = 86_400.0


def load_atom(atom_dir: str, name: str):
    path = ATOM_ROOT / atom_dir / "atom.py"
    # The before snapshot keeps 714's approved helper beside the atom. Insert
    # that directory exactly as the atom test harness does; the repaired tree
    # resolves the same import from the shared root package.
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


M714 = load_atom("714_الأرشفة", "contract_a714")
M715 = load_atom("715_ضغط_الأرشيف", "contract_a715")
M717 = load_atom("717_سلامة_البيانات", "contract_a717")
M800 = load_atom("800_النسخ_الاحتياطي", "contract_a800")
M802 = load_atom("802_أرشفة_الملفات", "contract_a802")
M803 = load_atom("803_تنظيف_الملفات", "contract_a803")


class Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class Bus:
    def __init__(self):
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, list] = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.events.append((name, payload))
        for handler in list(self.handlers.get(name, [])):
            result = handler(dict(payload))
            if inspect.isawaitable(result):
                await result

    def context(self, atom_id: int, config: dict[str, Any]) -> AtomContext:
        return AtomContext(atom_id, config, Logger(), self.publish, self.subscribe)

    def last(self, name: str) -> dict[str, Any]:
        return [payload for event, payload in self.events if event == name][-1]


def write_file(path: Path, content: bytes = b"data", mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def backup_cfg(tmp_path: Path, *, keep: int = 3, interval: int = 1) -> dict[str, Any]:
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    return {"backup_dir": str(tmp_path / "backups"), "source_dirs": [str(source)],
            "keep_last_n": keep, "interval_days": interval}


def archive_files_cfg(tmp_path: Path, *, interval: int = 1) -> dict[str, Any]:
    source = tmp_path / "files"
    source.mkdir(parents=True, exist_ok=True)
    return {"archive_dir": str(tmp_path / "file-archives"),
            "source_dirs": [str(source)], "older_than_days": 1,
            "interval_days": interval}


def cleanup_cfg(tmp_path: Path, *, older: int = 1, interval: int = 1,
                scan: Path | None = None) -> dict[str, Any]:
    scan = scan or (tmp_path / "scan")
    if scan == tmp_path / "scan":
        scan.mkdir(parents=True, exist_ok=True)
    return {"scan_dirs": [str(scan)], "older_than_days": older,
            "patterns": ["*.tmp"], "interval_days": interval}


async def start(module, atom_id: int, config: dict[str, Any], bus: Bus | None = None):
    bus = bus or Bus()
    atom = module.Atom()
    await atom.initialize(bus.context(atom_id, config))
    await atom.start()
    return atom, bus


@pytest.mark.asyncio
async def test_01_catchup_runs_without_sysday_after_two_days(tmp_path: Path):
    cfg = backup_cfg(tmp_path)
    write_file(Path(cfg["source_dirs"][0]) / "state.txt")
    atom, bus = await start(M800, 800, cfg)
    now = 10 * DAY
    atom._last_success = now - 2 * DAY
    await bus.publish("SYS_SECOND", {"official_time": now})
    assert atom.backup_count == 1 and bus.last(M800.EVENT_DONE)["trigger"] == "CATCHUP_REQUIRED"


@pytest.mark.asyncio
async def test_02_recent_success_is_skipped(tmp_path: Path):
    cfg = backup_cfg(tmp_path)
    write_file(Path(cfg["source_dirs"][0]) / "state.txt")
    atom, bus = await start(M800, 800, cfg)
    now = 10 * DAY
    atom._last_success = now - 3600
    await bus.publish("SYS_SECOND", {"official_time": now})
    assert atom.backup_count == 0 and atom._catchup_verdict["status"] == "SKIPPED"


@pytest.mark.asyncio
async def test_03_future_success_fails_closed_and_runs(tmp_path: Path):
    cfg = backup_cfg(tmp_path)
    write_file(Path(cfg["source_dirs"][0]) / "state.txt")
    atom, bus = await start(M800, 800, cfg)
    now = 10 * DAY
    atom._last_success = now + DAY
    await bus.publish("SYS_SECOND", {"official_time": now})
    assert atom.backup_count == 1
    assert bus.last(M800.EVENT_DONE)["trigger"] == "CATCHUP_REQUIRED"


@pytest.mark.asyncio
async def test_04_catchup_decides_once_per_start(tmp_path: Path):
    cfg = backup_cfg(tmp_path)
    write_file(Path(cfg["source_dirs"][0]) / "state.txt")
    atom, bus = await start(M800, 800, cfg)
    now = 10 * DAY
    await bus.publish("SYS_SECOND", {"official_time": now})
    await bus.publish("SYS_SECOND", {"official_time": now + DAY})
    assert atom.backup_count == 1


@pytest.mark.asyncio
async def test_05_interrupted_run_does_not_advance_stamp(tmp_path: Path, monkeypatch):
    cfg = backup_cfg(tmp_path)
    write_file(Path(cfg["source_dirs"][0]) / "state.txt")
    atom, bus = await start(M800, 800, cfg)
    atom._last_success = 100.0
    monkeypatch.setattr(atom, "_run_backup", lambda _now: (_ for _ in ()).throw(OSError("mid-run")))
    await bus.publish("SYS_SECOND", {"official_time": 10 * DAY})
    assert atom._last_success == 100.0 and atom.backup_count == 0


@pytest.mark.asyncio
async def test_06_next_start_retries_failed_run(tmp_path: Path, monkeypatch):
    cfg = backup_cfg(tmp_path)
    write_file(Path(cfg["source_dirs"][0]) / "state.txt")
    atom, bus = await start(M800, 800, cfg)
    monkeypatch.setattr(atom, "_run_backup", lambda _now: (_ for _ in ()).throw(OSError("mid-run")))
    await bus.publish("SYS_SECOND", {"official_time": 10 * DAY})
    snap = await atom.snapshot()
    atom2, bus2 = await start(M800, 800, cfg)
    await atom2.restore(snap)
    await atom2.stop(); await atom2.start()
    await bus2.publish("SYS_SECOND", {"official_time": 10 * DAY + 1})
    assert atom2.backup_count == 1 and atom2._last_success == 10 * DAY + 1


@pytest.mark.asyncio
async def test_07_cleanup_zero_days_deletes_nothing_and_degrades(tmp_path: Path):
    cfg = cleanup_cfg(tmp_path, older=0)
    victim = Path(cfg["scan_dirs"][0]) / "today.tmp"
    write_file(victim, mtime=10 * DAY - 10)
    atom, bus = await start(M803, 803, cfg)
    await bus.publish("SYS_DAY", {"official_time": 10 * DAY})
    assert victim.exists() and atom._last_success is None
    assert (await atom.health_check()).state == HealthState.DEGRADED


@pytest.mark.asyncio
async def test_08_interrupted_cleanup_does_not_advance_stamp(tmp_path: Path, monkeypatch):
    cfg = cleanup_cfg(tmp_path)
    atom, bus = await start(M803, 803, cfg)
    atom._last_success = 100.0
    monkeypatch.setattr(atom, "_run_cleanup", lambda _now: (_ for _ in ()).throw(OSError("scan died")))
    await bus.publish("SYS_SECOND", {"official_time": 10 * DAY})
    assert atom._last_success == 100.0


@pytest.mark.asyncio
async def test_09_pulse_without_official_time_does_no_work(tmp_path: Path):
    configs = [(M800, 800, backup_cfg(tmp_path / "b")),
               (M802, 802, archive_files_cfg(tmp_path / "a")),
               (M803, 803, cleanup_cfg(tmp_path / "c"))]
    for module, atom_id, cfg in configs:
        atom, bus = await start(module, atom_id, cfg)
        await bus.publish("SYS_SECOND", {"timestamp": 10 * DAY})
        assert atom._last_success is None and atom._catchup_done is False


@pytest.mark.asyncio
async def test_10_backup_retention_under_pressure(tmp_path: Path):
    cfg = backup_cfg(tmp_path, keep=3)
    write_file(Path(cfg["source_dirs"][0]) / "state.txt")
    atom, bus = await start(M800, 800, cfg)
    for index in range(5):
        await bus.publish("SYS_DAY", {"official_time": (10 + index) * DAY})
    backups = list(Path(cfg["backup_dir"]).glob("backup_*.tar.gz"))
    assert atom.backup_count == 5 and len(backups) == 3


@pytest.mark.asyncio
async def test_11_missing_backup_source_is_announced(tmp_path: Path):
    cfg = backup_cfg(tmp_path)
    Path(cfg["source_dirs"][0]).rmdir()
    atom, bus = await start(M800, 800, cfg)
    await bus.publish("SYS_DAY", {"official_time": 10 * DAY})
    assert atom.backup_count == 0 and bus.last(M800.EVENT_FAILED)["reason"]
    assert (await atom.health_check()).state == HealthState.DEGRADED


@pytest.mark.asyncio
async def test_12_missing_cleanup_path_degrades_without_random_delete(tmp_path: Path):
    safe = tmp_path / "safe.tmp"
    write_file(safe, mtime=1.0)
    cfg = cleanup_cfg(tmp_path, scan=tmp_path / "does-not-exist")
    atom, bus = await start(M803, 803, cfg)
    await bus.publish("SYS_SECOND", {"official_time": 10 * DAY})
    assert safe.exists() and atom._last_success is None
    assert (await atom.health_check()).state == HealthState.DEGRADED


@pytest.mark.asyncio
async def test_13_corrupt_compression_keeps_original(tmp_path: Path, monkeypatch):
    source = tmp_path / "closed.db"
    write_file(source, b"archive-data" * 1000)
    cfg = {"min_size_bytes": 0, "compression_level": 6,
           "keep_original": False, "active_archive_db_path": str(tmp_path / "active.db")}
    atom, bus = await start(M715, 715, cfg)
    monkeypatch.setattr(atom, "_verify_gzip", lambda *_args: False)
    await bus.publish(M715.EVENT_IN, {"rows": 1, "archive_path": str(source)})
    assert source.exists() and not Path(str(source) + ".gz").exists()
    assert bus.last(M715.EVENT_OUT)["compressed"] is False
    assert (await atom.health_check()).state == HealthState.DEGRADED


def make_db(path: Path, table: str = "events", stamp: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, occurred_at REAL)")
        connection.execute(f"INSERT INTO {table} (occurred_at) VALUES (?)", (stamp,))
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_14_active_database_is_rejected(tmp_path: Path):
    active = tmp_path / "archive.db"
    make_db(active)
    cfg = {"min_size_bytes": 0, "compression_level": 6,
           "keep_original": True, "active_archive_db_path": str(active)}
    atom, bus = await start(M715, 715, cfg)
    await bus.publish(M715.EVENT_IN, {"rows": 1, "archive_path": str(active)})
    result = bus.last(M715.EVENT_OUT)
    assert result["reason"] == "ACTIVE_ARCHIVE_PATH"
    assert active.exists() and not Path(str(active) + ".gz").exists()
    locked = tmp_path / "other-live.db"; make_db(locked)
    connection = sqlite3.connect(locked, timeout=0.0)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        await bus.publish(M715.EVENT_IN, {"rows": 1, "archive_path": str(locked)})
        assert bus.last(M715.EVENT_OUT)["reason"] == "ACTIVE_ARCHIVE_LOCKED"
        assert locked.exists() and not Path(str(locked) + ".gz").exists()
    finally:
        connection.rollback(); connection.close()


@pytest.mark.asyncio
async def test_15_same_path_is_not_recompressed(tmp_path: Path):
    source = tmp_path / "closed.bin"
    write_file(source, b"archive-data" * 1000)
    cfg = {"min_size_bytes": 0, "compression_level": 6,
           "keep_original": True, "active_archive_db_path": str(tmp_path / "active.db")}
    atom, bus = await start(M715, 715, cfg)
    event = {"rows": 1, "archive_path": str(source)}
    await bus.publish(M715.EVENT_IN, event)
    first_mtime = Path(str(source) + ".gz").stat().st_mtime_ns
    await bus.publish(M715.EVENT_IN, event)
    assert bus.last(M715.EVENT_OUT)["reason"] == "ALREADY_PROCESSED"
    assert Path(str(source) + ".gz").stat().st_mtime_ns == first_mtime
    assert not Path(str(source) + ".gz.gz").exists()


@pytest.mark.asyncio
async def test_16_integrity_guard_rejects_malicious_identifier(tmp_path: Path):
    db = tmp_path / "trades.db"
    make_db(db, "trades")
    cfg = {"stores": [{"db_path": str(db),
                       "table": "trades; DROP TABLE trades;--",
                       "time_column": "occurred_at"}],
           "warn_on_empty_table": False}
    atom, bus = await start(M717, 717, cfg)
    await bus.publish(M717.EVENT_IN, {"timestamp": 10.0})
    assert "UNREADABLE" in bus.last(M717.EVENT_OUT)["flags"]
    connection = sqlite3.connect(db)
    try:
        assert connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    finally:
        connection.close()


def archive_cfg(tmp_path: Path, source: Path, active: Path) -> dict[str, Any]:
    return {"stores": [{"db_path": str(source), "table": "events",
                        "time_column": "occurred_at"}],
            "archive_db_path": str(active), "archive_after_days": 1,
            "batch_limit": 100}


@pytest.mark.asyncio
async def test_17_rotation_publishes_only_closed_database(tmp_path: Path):
    source, active = tmp_path / "source.db", tmp_path / "archive.db"
    make_db(source)
    atom, bus = await start(M714, 714, archive_cfg(tmp_path, source, active))
    await bus.publish(M714.EVENT_DAY, {"official_time": 10 * DAY})
    result = bus.last(M714.EVENT_OUT)
    assert result["archive_path"] != str(active)
    assert Path(result["archive_path"]).is_file() and active.is_file()
    assert result["status"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_18_closed_database_passes_independent_compression_guard(tmp_path: Path):
    source, active = tmp_path / "source.db", tmp_path / "archive.db"
    make_db(source)
    bus = Bus()
    a714, _ = await start(M714, 714, archive_cfg(tmp_path, source, active), bus)
    c715 = {"min_size_bytes": 0, "compression_level": 6,
            "keep_original": True, "active_archive_db_path": str(active)}
    a715, _ = await start(M715, 715, c715, bus)
    await bus.publish(M714.EVENT_DAY, {"official_time": 10 * DAY})
    closed = bus.last(M714.EVENT_OUT)["archive_path"]
    compressed = bus.last(M715.EVENT_OUT)
    assert compressed["compressed"] is True and compressed["verified"] is True
    assert Path(str(closed) + ".gz").is_file()


@pytest.mark.asyncio
async def test_19_guard_rejects_live_database_even_without_rotation(tmp_path: Path):
    active = tmp_path / "archive.db"
    make_db(active)
    cfg = {"min_size_bytes": 0, "compression_level": 6,
           "keep_original": False, "active_archive_db_path": str(active)}
    atom, bus = await start(M715, 715, cfg)
    await atom._on_archived({"rows": 1, "archive_path": str(active),
                             "active_archive_path": str(active)})
    assert bus.last(M715.EVENT_OUT)["reason"].startswith("ACTIVE_ARCHIVE")
    assert active.exists()


@pytest.mark.asyncio
async def test_20_archiving_continues_after_closed_database_is_compressed(tmp_path: Path):
    source, active = tmp_path / "source.db", tmp_path / "archive.db"
    make_db(source, stamp=1.0)
    bus = Bus()
    a714, _ = await start(M714, 714, archive_cfg(tmp_path, source, active), bus)
    c715 = {"min_size_bytes": 0, "compression_level": 6,
            "keep_original": True, "active_archive_db_path": str(active)}
    await start(M715, 715, c715, bus)
    await bus.publish(M714.EVENT_DAY, {"official_time": 10 * DAY})
    first_closed = bus.last(M714.EVENT_OUT)["archive_path"]
    assert Path(str(first_closed) + ".gz").is_file()
    connection = sqlite3.connect(source)
    try:
        connection.execute("INSERT INTO events (occurred_at) VALUES (?)", (2.0,))
        connection.commit()
    finally:
        connection.close()
    await bus.publish(M714.EVENT_DAY, {"official_time": 11 * DAY})
    second = bus.last(M714.EVENT_OUT)
    assert second["status"] == "ARCHIVED" and second["archive_path"] != first_closed
    assert active.is_file() and Path(str(second["archive_path"]) + ".gz").is_file()
