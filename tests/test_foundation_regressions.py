from __future__ import annotations

import asyncio
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

from core.contracts.atom import AtomContext

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

class Logger:
    def __getattr__(self, _name): return lambda *args, **kwargs: None
class Bus:
    def __init__(self): self.events=[]
    async def publish(self,name,payload): self.events.append((name,payload))
    def subscribe(self,*args): pass

def load(atom_id: int):
    folder=next((ATOM_ROOT).glob(f'{atom_id}_*'));sys.path.insert(0,str(folder))
    try:
        spec=importlib.util.spec_from_file_location(f'foundation_{atom_id}',folder/'atom.py')
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
    finally:sys.path.pop(0)

@pytest.mark.asyncio
async def test_current_account_writer_handles_two_scoped_accounts() -> None:
    module=load(601);bus=Bus()
    with tempfile.TemporaryDirectory() as directory:
        db=str(Path(directory)/'bridge.db');atom=module.Atom()
        cfg={'account_id':'CURRENT','db_path':db,'heartbeat_interval_s':999,
             'blocked_account_ids':[],'require_symbol_resolution':False,'magic':20260801}
        await atom.initialize(AtomContext(601,cfg,Logger(),bus.publish,bus.subscribe))
        connection=sqlite3.connect(db);connection.execute('CREATE TABLE account_v2(account_id TEXT PRIMARY KEY)')
        connection.executemany('INSERT INTO account_v2 VALUES (?)',[('A',),('B',)]);connection.commit();connection.close()
        await atom.start()
        try:
            for account in ('A','B'):
                await atom._on_final_decision({'account_id':account,'magic':20260801,'request_id':'r-'+account,
                    'action':'OPEN','symbol':'X','side':'BUY','volume':1})
            connection=sqlite3.connect(db);rows=connection.execute(
                'SELECT account_id,request_id FROM commands ORDER BY account_id').fetchall();connection.close()
            assert rows==[('A','r-A'),('B','r-B')]
        finally:await atom.stop()

@pytest.mark.asyncio
async def test_portfolio_store_expands_accounts_list() -> None:
    module=load(709);bus=Bus()
    with tempfile.TemporaryDirectory() as directory:
        db=str(Path(directory)/'portfolio.db');atom=module.Atom()
        cfg={'db_path':db,'min_write_interval_s':0,'retention_days':7}
        await atom.initialize(AtomContext(709,cfg,Logger(),bus.publish,bus.subscribe));await atom.start()
        await atom._on_summary({'timestamp':10,'accounts':[{'account_id':'A','equity':1},{'account_id':'B','equity':2}]})
        connection=sqlite3.connect(db);rows=connection.execute('SELECT account_id,equity FROM portfolio ORDER BY account_id').fetchall();connection.close()
        assert rows==[('A',1.0),('B',2.0)]

@pytest.mark.asyncio
async def test_price_receiver_rejects_non_finite_and_inverted_ticks() -> None:
    module=load(102);bus=Bus();atom=module.Atom()
    await atom.initialize(AtomContext(102,{},Logger(),bus.publish,bus.subscribe));await atom.start()
    await atom._on_tick({'account_id':'A','symbol':'X','bid':float('nan'),'ask':2})
    await atom._on_tick({'account_id':'A','symbol':'X','bid':3,'ask':2})
    await atom._on_tick({'account_id':'A','symbol':'X','bid':1,'ask':2})
    outputs=[payload for name,payload in bus.events if name==module.EVENT_OUT]
    assert len(outputs)==1 and outputs[0]['account_id']=='A' and atom.dropped_count==2
