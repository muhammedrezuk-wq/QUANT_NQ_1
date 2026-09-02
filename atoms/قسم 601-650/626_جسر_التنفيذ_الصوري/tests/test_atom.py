import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom626", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom626"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

import pytest
from pathlib import Path
from core.contracts.atom import AtomContext


class _Log:
    def __getattr__(self, _n): return lambda *a, **k: None


ORDER = {"request_id": "o1", "account_id": "A", "broker": "BR", "symbol": "NQ",
         "action": "OPEN", "side": "BUY", "volume": 1.0, "reference_price": 50.0}


@pytest.mark.asyncio
async def test_fill_from_recorded_spread():
    out: list[dict] = []
    async def pub(n, p):
        if isinstance(p, dict) and "entry_price" in p: out.append(p)
    a = Atom()
    await a.initialize(AtomContext(626, {"per_lot_commission": 5.0}, _Log(), pub, lambda *x: None))
    await a.start()
    await a._on_tick({"symbol": "NQ", "bid": 49.9, "ask": 50.1})
    await a._on_order(dict(ORDER))
    assert out[0]["entry_price"] == pytest.approx(50.1)
    assert out[0]["slippage_source"] == "recorded_spread"
    assert out[0]["commission"] == pytest.approx(5.0)
