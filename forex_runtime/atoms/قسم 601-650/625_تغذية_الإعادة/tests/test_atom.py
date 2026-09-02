import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom625", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom625"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

import json
import sqlite3
import pytest
from pathlib import Path
from core.contracts.atom import AtomContext


class _Log:
    def __getattr__(self, _n): return lambda *a, **k: None


def _db(tmp_path: Path, rows: int = 3) -> Path:
    p = tmp_path / "market_data.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE market_data (id INTEGER PRIMARY KEY, symbol TEXT, provider TEXT,"
              " bid REAL, ask REAL, occurred_at REAL, payload_json TEXT)")
    for i in range(1, rows + 1):
        c.execute("INSERT INTO market_data VALUES (?,?,?,?,?,?,?)",
                  (i, "NQ", "CTRADER", 99.9 + i, 100.1 + i, 5.0 + i,
                   json.dumps({"account_id": "A", "broker": "BR", "symbol": "NQ",
                               "price": 100.0 + i, "volume": 1})))
    c.commit(); c.close()
    return p


@pytest.mark.asyncio
async def test_publishes_rows_with_deterministic_identity(tmp_path):
    out: list[tuple[str, dict]] = []
    async def pub(n, p): out.append((n, p))
    a = Atom()
    await a.initialize(AtomContext(625, {"db_path": str(_db(tmp_path))}, _Log(), pub, lambda *x: None))
    await a.start(); await a._on_start({})
    ticks = [p for n, p in out if n == "feed.replay.tick"]
    assert len(ticks) == 3
    assert ticks[0]["tick_id"] == "replay-1" and ticks[0]["sequence"] == "replay-1"
    assert ticks[2]["tick_id"] == "replay-3"
    state = [p for n, p in out if n == "replay.session.state"][-1]
    assert state["status"] == "DONE" and state["published"] == 3


@pytest.mark.asyncio
async def test_manual_start_does_not_replay_by_itself(tmp_path):
    out: list[tuple[str, dict]] = []
    async def pub(n, p): out.append((n, p))
    a = Atom()
    await a.initialize(AtomContext(625, {"db_path": str(_db(tmp_path))}, _Log(), pub, lambda *x: None))
    await a.start()  # بلا أمر جلسة — لا شيء يُنشر
    assert not [p for n, p in out if n == "feed.replay.tick"]
    health = await a.health_check()
    assert health.details["dropped"] == 0
