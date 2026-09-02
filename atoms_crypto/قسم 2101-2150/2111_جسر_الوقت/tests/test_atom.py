import asyncio, inspect, os, sys, time
from pathlib import Path as P
sys.path.insert(0,str(P(__file__).resolve().parents[4]));sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clock
from core.contracts.atom import AtomContext,HealthState
import importlib.util as I
sp=I.spec_from_file_location('a111',P(__file__).resolve().parents[1]/'atom.py');m=I.module_from_spec(sp);sys.modules['a111']=m;sp.loader.exec_module(m)
CFG={'max_age_s':5,'divergence_threshold_s':.5}
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[];self.h={}
 def subscribe(self,n,h):self.h.setdefault(n,[]).append(h)
 async def publish(self,n,p):
  self.e.append((n,p))
  for h in self.h.get(n,[]):
   r=h(p)
   if inspect.isawaitable(r):await r
 def ctx(self):return AtomContext(111,dict(CFG),L(),self.publish,self.subscribe)
async def new():
 clock.reset_for_tests();clock.configure(max_accepted_offset_s=5,max_sample_age_s=30,stale_after_s=900,max_slew_per_second=.05);assert clock.accept_sample({'median_offset_s':.1,'measured_at':time.time(),'quorum':True},writer='003')[0]
 b=B();a=m.Atom();await a.initialize(b.ctx());await a.start();return a,b
def sync():return {'offset_s':.1,'effective_offset_s':0,'target_offset_s':.1,'measured_at':time.time(),'clock_quality':'SYNCED','clock_sequence':1,'sync_age_s':0}

async def test_relays_only_valid_canonical_time():
 a,b=await new();await b.publish(m.EVENT_IN_SYNCED,sync());assert [p for n,p in b.e if n==m.EVENT_OUT_SYNCED]
 await b.publish(m.EVENT_IN_SYNCED,{'offset_s':'bad'});assert a.rejected_count==1

async def test_detects_clock_vs_event_bus_divergence():
 a,b=await new();now=clock.now();await a._on_heartbeat({'official_time':now,'timestamp':now-2,'clock_quality':'SYNCED'})
 out=[p for n,p in b.e if n==m.EVENT_DIVERGENCE]
 assert len(out)==1 and out[0]['divergence_s']>=2 and out[0]['status']=='DEGRADED'
 assert (await a.health_check()).state==HealthState.DEGRADED

async def test_health_requires_fresh_heartbeat_and_synced_clock():
 a,b=await new();assert (await a.health_check()).state==HealthState.DEGRADED
 await a._on_synced(sync());await a._on_heartbeat({'official_time':clock.now(),'timestamp':clock.now(),'clock_quality':'SYNCED'})
 assert (await a.health_check()).state==HealthState.HEALTHY

async def test_ignores_before_start():
 clock.reset_for_tests();b=B();a=m.Atom();await a.initialize(b.ctx());await a._on_synced(sync());assert not b.e

async def main():
 for t in (test_relays_only_valid_canonical_time,test_detects_clock_vs_event_bus_divergence,
 test_health_requires_fresh_heartbeat_and_synced_clock,test_ignores_before_start):await t()
if __name__=='__main__':asyncio.run(main())
