from __future__ import annotations
import asyncio, importlib.util, inspect, json, sqlite3, sys
from pathlib import Path
from typing import Any
import pytest
from core.contracts.atom import AtomContext,HealthState
ROOT=Path(__file__).resolve().parents[1];DAY=86400.0
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)


def load(d,n):
 p=ATOM_ROOT/d/'atom.py';s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);sys.modules[n]=m;s.loader.exec_module(m);return m
M701=load('701_مخزن_بيانات_السوق','stores701');M702=load('702_مخزن_الصفقات','stores702');M704=load('704_مخزن_الخط_الزمني','stores704');M708=load('708_سجل_الرموز','stores708');M716=load('716_تنظيف_التكرار','stores716')
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[];self.h={}
 def subscribe(self,n,h):self.h.setdefault(n,[]).append(h)
 async def publish(self,n,p):
  self.e.append((n,p))
  for h in list(self.h.get(n,[])):
   r=h(dict(p))
   if inspect.isawaitable(r):await r
 def ctx(self,i,c):return AtomContext(i,c,L(),self.publish,self.subscribe)
 def last(self,n):return [p for e,p in self.e if e==n][-1]
async def start(m,i,c,b=None):
 b=b or B();a=m.Atom();await a.initialize(b.ctx(i,c));await a.start();return a,b
def c701(tmp,**kw):
 c={'db_path':str(tmp/'market.db'),'flush_size':100,'retention_days':1,'flush_interval_s':.05,'max_rows':1000,'max_db_bytes':10000000};c.update(kw);return c
def c704(tmp,**kw):
 c={'db_path':str(tmp/'timeline.db'),'watch_events':['platform.trade_event','execution.order.rejected','emergency.halt','other.event'],'flush_size':100,'retention_days':1,'flush_interval_s':.05,'immediate_events':['platform.trade_event','execution.order.rejected','emergency.halt'],'max_rows':1000,'max_db_bytes':10000000};c.update(kw);return c
def count(path,table):
 c=sqlite3.connect(path)
 try:return c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
 finally:c.close()

def trade_cfg(tmp):return {'db_path':str(tmp/'trades.db'),'max_rows':1000}
def trade_event(row=1):return {'event_type':'OPENED','source_row_id':row,'ticket':row,'symbol':'NQ','timestamp':float(row)}

@pytest.mark.asyncio
async def test_01_periodic_flush_survives_without_stop(tmp_path):
 a,b=await start(M701,701,c701(tmp_path))
 for i in range(10):await b.publish(M701.EVENT_IN,{'symbol':'NQ','timestamp':i})
 assert count(a._db_path,'market_data')==0
 await asyncio.sleep(.12);assert count(a._db_path,'market_data')==10 and not a._buffer
 await a.stop()
@pytest.mark.asyncio
async def test_02_critical_timeline_event_is_immediate(tmp_path):
 a,b=await start(M704,704,c704(tmp_path));await b.publish('execution.order.rejected',{'request_id':'r','timestamp':1})
 assert count(a._db_path,'timeline')==1 and not a._buffer;await a.stop()
@pytest.mark.asyncio
async def test_03_prune_catches_up_without_sysday(tmp_path):
 a,b=await start(M701,701,c701(tmp_path,flush_interval_s=10))
 await b.publish(M701.EVENT_IN,{'symbol':'NQ','timestamp':1});await b.publish(M701.EVENT_IN,{'symbol':'NQ','timestamp':10*DAY});await a._flush_and_report(None)
 a._last_prune_success=7*DAY;await b.publish('SYS_SECOND',{'official_time':10*DAY})
 assert count(a._db_path,'market_data')==1 and a._last_prune_success==10*DAY;await a.stop()
@pytest.mark.asyncio
async def test_04_recent_prune_is_skipped(tmp_path):
 a,b=await start(M701,701,c701(tmp_path,flush_interval_s=10));await b.publish(M701.EVENT_IN,{'symbol':'NQ','timestamp':1});await a._flush_and_report(None)
 a._last_prune_success=10*DAY-3600;await b.publish('SYS_SECOND',{'official_time':10*DAY})
 assert count(a._db_path,'market_data')==1 and a._catchup_verdict['status']=='SKIPPED';await a.stop()
@pytest.mark.asyncio
async def test_05_thirty_daily_restarts_stay_bounded(tmp_path):
 a,b=await start(M701,701,c701(tmp_path,flush_interval_s=10,max_rows=20,retention_days=0))
 for day in range(30):
  await a.stop();await a.start()
  for j in range(2):await b.publish(M701.EVENT_IN,{'symbol':'NQ','timestamp':day*DAY+j})
  await a._flush_and_report(None)
 assert count(a._db_path,'market_data')<=20 and Path(a._db_path).stat().st_size<1000000;await a.stop()
@pytest.mark.asyncio
async def test_06_max_rows_forces_prune_and_degrades(tmp_path):
 a,b=await start(M701,701,c701(tmp_path,flush_interval_s=10,max_rows=3))
 for i in range(5):await b.publish(M701.EVENT_IN,{'symbol':'NQ','timestamp':i})
 await a._flush_and_report(None);assert count(a._db_path,'market_data')==3
 assert (await a.health_check()).state==HealthState.DEGRADED;await a.stop()
@pytest.mark.asyncio
async def test_07_trade_dedupe_remains_database_enforced(tmp_path):
 a,b=await start(M702,702,trade_cfg(tmp_path));e=trade_event();await b.publish(M702.EVENT_TRADE,e);await b.publish(M702.EVENT_TRADE,e)
 assert count(a._db_path,'trades')==1 and a.duplicate_count==1
@pytest.mark.asyncio
async def test_08_concurrent_trade_duplicates_are_one_row(tmp_path):
 a,b=await start(M702,702,trade_cfg(tmp_path));e=trade_event();await asyncio.gather(b.publish(M702.EVENT_TRADE,e),b.publish(M702.EVENT_TRADE,e))
 assert count(a._db_path,'trades')==1
@pytest.mark.asyncio
async def test_09_symbol_specs_survive_restart(tmp_path):
 cfg={'strip_suffixes':[],'min_stem_length':1,'passthrough_unknown':True,'broker_map':{},'canonical_map':{'NQ':['NQ']},'canonical_patterns':{}}
 a,b=await start(M708,708,cfg);await b.publish(M708.EVENT_SPECS,{'account_id':'A','symbols':[{'symbol':'NQ','contract_size':20}]});snap=await a.snapshot();a2,b2=await start(M708,708,cfg);await a2.restore(snap)
 await b2.publish(M708.EVENT_RESOLVE_REQUESTED_NEW,{'request_id':'r','account_id':'A','logical_symbol':'NQ'})
 assert b2.last(M708.EVENT_RESOLVED_NEW)['spec']['contract_size']==20
@pytest.mark.asyncio
async def test_10_missing_specs_are_announced_not_zero(tmp_path):
 cfg={'strip_suffixes':[],'min_stem_length':1,'passthrough_unknown':True,'broker_map':{},'canonical_map':{'NQ':['NQ']},'canonical_patterns':{}}
 a,b=await start(M708,708,cfg);await b.publish(M708.EVENT_RESOLVE_REQUESTED_NEW,{'request_id':'r','account_id':'A','logical_symbol':'NQ'})
 out=b.last(M708.EVENT_RESOLVED_NEW);assert out['approved'] is False and out['reason']=='SYMBOL_UNRESOLVED' and 'spec' not in out
@pytest.mark.asyncio
async def test_11_dedupe_failure_does_not_advance_checkpoint(tmp_path,monkeypatch):
 db=tmp_path/'d.db';c=sqlite3.connect(db);c.execute('create table t(id integer primary key,k text)');c.executemany('insert into t(k) values(?)',[('x',),('x',)]);c.commit();c.close()
 cfg={'stores':[{'db_path':str(db),'table':'t','dedup_columns':['k']}],'vacuum_after_cleanup':False};a,b=await start(M716,716,cfg)
 original=a._clean_one;monkeypatch.setattr(a,'_clean_one',lambda _s:(_ for _ in()).throw(OSError('died')));await b.publish(M716.EVENT_IN,{'timestamp':1});assert a._last_success is None and a._next_store_index==0
 monkeypatch.setattr(a,'_clean_one',original);await b.publish(M716.EVENT_IN,{'timestamp':2});assert a._last_success==2 and count(db,'t')==1
@pytest.mark.asyncio
async def test_12_zero_retention_remains_fail_safe(tmp_path):
 a,b=await start(M701,701,c701(tmp_path,retention_days=0,flush_interval_s=10));await b.publish(M701.EVENT_IN,{'symbol':'NQ','timestamp':1});await a._flush_and_report(None);await b.publish('SYS_DAY',{'official_time':100*DAY});assert count(a._db_path,'market_data')==1;await a.stop()
@pytest.mark.asyncio
async def test_13_flush_failure_keeps_buffer(tmp_path,monkeypatch):
 a,b=await start(M701,701,c701(tmp_path,flush_interval_s=10));await b.publish(M701.EVENT_IN,{'symbol':'NQ','timestamp':1});monkeypatch.setattr(a,'_connect',lambda:(_ for _ in()).throw(sqlite3.OperationalError('disk full')));assert a._flush()==0 and len(a._buffer)==1;await a.stop()
@pytest.mark.asyncio
async def test_14_all_concurrent_timeline_events_are_recorded(tmp_path):
 a,b=await start(M704,704,c704(tmp_path));await asyncio.gather(*(b.publish('platform.trade_event',{'ticket':i,'timestamp':i}) for i in range(11)));assert count(a._db_path,'timeline')==11;await a.stop()
