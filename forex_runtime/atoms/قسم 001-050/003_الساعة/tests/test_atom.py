import asyncio, inspect, os, sys, time
from pathlib import Path as P
sys.path.insert(0,str(P(__file__).resolve().parents[3]));sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clock
from core.contracts.atom import AtomContext,HealthState
import importlib.util as I
sp=I.spec_from_file_location('a003',P(__file__).resolve().parents[1]/'atom.py');m=I.module_from_spec(sp);sys.modules['a003']=m;sp.loader.exec_module(m)
CFG={'sys_tick_interval_s':.1,'heartbeat_interval_s':1,'drift_alert_s':1,
'max_accepted_offset_s':5,'max_sample_age_s':30,'stale_after_s':900,'max_slew_per_second':.05}
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
 def ctx(self):return AtomContext(3,dict(CFG),L(),self.publish,self.subscribe)
async def new(start=True):
 clock.reset_for_tests();b=B();a=m.Atom();await a.initialize(b.ctx())
 if start:await a.start()
 return a,b
def sample(offset=.3):return {'sample_id':1,'measured_at':time.time(),'median_offset_s':offset,'quorum':True}

async def test_sample_updates_shared_clock_and_publishes_approved_state():
 a,b=await new();await b.publish(m.EVENT_SAMPLE,sample(.3));out=[p for n,p in b.e if n==m.EVENT_SYNCED]
 assert len(out)==1 and out[0]['offset_s']==.3 and clock.state()['sequence']==1
 await a.stop()

async def test_non_finite_and_malformed_samples_are_rejected():
 a,b=await new();await a._on_sample(sample(float('nan')));await a._on_sample({'median_offset_s':.1})
 assert a.rejected_samples==2 and clock.state()['sequence']==0;await a.stop()

async def test_start_is_idempotent_and_stop_stops_all_pulses():
 a,b=await new();tasks=(a._sys_tick_task,a._heartbeat_task,a._minute_task);await a.start()
 assert tasks==(a._sys_tick_task,a._heartbeat_task,a._minute_task)
 await asyncio.sleep(.12);await a.stop();count=len(b.e);sequence=clock.state()['sequence']
 await b.publish(m.EVENT_SAMPLE,{**sample(.4),'sample_id':2});await asyncio.sleep(.12)
 assert len(b.e)==count+1 and clock.state()['sequence']==sequence

async def test_ticks_use_shared_clock_and_health_degrades_without_sync():
 a,b=await new();await a._on_sys_tick();tick=[p for n,p in b.e if n==m.EVENT_SYS_TICK][-1]
 assert tick['clock_quality']==clock.LOCAL_FALLBACK and isinstance(tick['monotonic_time'],float)
 assert (await a.health_check()).state==HealthState.DEGRADED;await a.stop()

async def main():
 for t in (test_sample_updates_shared_clock_and_publishes_approved_state,
 test_non_finite_and_malformed_samples_are_rejected,test_start_is_idempotent_and_stop_stops_all_pulses,
 test_ticks_use_shared_clock_and_health_degrades_without_sync):await t()
if __name__=='__main__':asyncio.run(main())
