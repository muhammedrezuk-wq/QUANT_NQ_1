from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_backup_archive_contract import Bus, M715, M717, make_db, start

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



@pytest.mark.asyncio
async def test_legacy_01_missing_compression_source_is_reported(tmp_path: Path):
    cfg = {"min_size_bytes": 0, "compression_level": 6, "keep_original": True}
    atom, bus = await start(M715, 715, cfg)
    await bus.publish(M715.EVENT_IN, {"rows": 1,
                                      "archive_path": str(tmp_path / "missing.db")})
    result = bus.last(M715.EVENT_OUT)
    assert result["compressed"] is False and result["reason"] == "MISSING"


@pytest.mark.asyncio
async def test_legacy_02_zero_rows_does_not_compress(tmp_path: Path):
    source = tmp_path / "archive.db"; source.write_bytes(b"data" * 100)
    cfg = {"min_size_bytes": 0, "compression_level": 6, "keep_original": True}
    atom, bus = await start(M715, 715, cfg)
    await bus.publish(M715.EVENT_IN, {"rows": 0, "archive_path": str(source)})
    assert not Path(str(source) + ".gz").exists()
    assert not [p for n, p in bus.events if n == M715.EVENT_OUT]


@pytest.mark.asyncio
async def test_legacy_03_integrity_reports_clean_table_sound(tmp_path: Path):
    db = tmp_path / "events.db"; make_db(db)
    cfg = {"stores": [{"db_path": str(db), "table": "events",
                       "time_column": "occurred_at"}],
           "warn_on_empty_table": False}
    atom, bus = await start(M717, 717, cfg)
    await bus.publish(M717.EVENT_IN, {"timestamp": 10.0})
    result = bus.last(M717.EVENT_OUT)
    assert result["verdict"] == "SOUND" and result["flags"] == []


def test_legacy_04_integrity_identifier_guard_remains_present():
    source = (ATOM_ROOT / "717_سلامة_البيانات" / "atom.py").read_text("utf-8")
    assert "_IDENT" in source and "^[A-Za-z0-9_]+$" in source
