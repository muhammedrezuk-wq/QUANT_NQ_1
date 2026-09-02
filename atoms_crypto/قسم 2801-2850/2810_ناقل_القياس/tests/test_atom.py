import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom810", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom810"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

import pytest  # noqa: E402


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


class _Ctx:
    def __init__(self, cfg, bus=None):
        self.config = cfg
        self.handlers = []
    def subscribe(self, event, handler):
        self.handlers.append((event, handler))
    async def publish(self, event, payload):
        self.last = (event, payload)

@pytest.mark.asyncio
async def test_rows_buffered_and_flushed(tmp_path):
    a = Atom()
    ctx = _Ctx({"out_dir": str(tmp_path / "tel"), "batch_size": 3,
                "flush_interval_s": 1, "max_files": 2, "max_buffer": 10})
    firehose: list = []
    # شكل مُقلِع النواة حرفيًّا (bootloader): يقبل handler فقط، والهوية من رقم
    # الذرّة — أي وسيط إضافي يُسقط الذرّة عند الإقلاع الحقيقي (درس 2026-08-23).
    def subscribe_all(handler):
        firehose.append(handler)
    await a.initialize(AtomContext(810, ctx.config, _Log(), ctx.publish,
                                   ctx.subscribe, subscribe_all=subscribe_all))
    await a.start()
    for i in range(3):
        await firehose[0]("market.tick.validated",
                           {"timestamp": 1.0, "trace_id": "t", "symbol": "NQ"})
    assert a._rows_seen == 3 and len(a._buffer) == 3
    await a._flush()
    assert a._batches == 1 and a._rows_written == 3
    assert list((tmp_path / "tel").rglob("*.jsonl.gz"))
