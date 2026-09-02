import asyncio, os, sys, time
from pathlib import Path as P
sys.path.insert(0,str(P(__file__).resolve().parents[3]));sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clock
from core.contracts.atom import AtomContext,HealthState
import importlib.util as I
sp=I.spec_from_file_location('a806',P(__file__).resolve().parents[1]/'atom.py');m=I.module_from_spec(sp);sys.modules['a806']=m;sp.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
 def ctx(self):return AtomContext(806,{},L(),self.publish,self.subscribe)
def sync_clock():
 clock.reset_for_tests();clock.configure(max_accepted_offset_s=5,max_sample_age_s=30,stale_after_s=900,max_slew_per_second=.05);assert clock.accept_sample({'median_offset_s':.2,'measured_at':time.time(),'quorum':True},writer='003')[0]

async def test_five_cadences_defined():
 assert set(m._CADENCES)=={'SYS_SECOND','SYS_5MIN','SYS_15MIN','SYS_HOUR','SYS_DAY'}

async def test_emit_has_stable_pulse_id_and_clock_fields():
 sync_clock();b=B();a=m.Atom();await a.initialize(b.ctx());a._running=True;event='SYS_DAY';interval=m._CADENCES[event];bucket=int(clock.now()//interval);a._last_bucket[event]=bucket-1;await a._emit_tick(event)
 body=[p for n,p in b.e if n==event][-1];assert body['pulse_id']==f'{event}|{int(body["bucket_start"])}'
 required={'official_time','monotonic_time','sequence','time_source','sync_age_s',
           'offset_s','clock_quality','pulse_id','missed_intervals'}
 assert required <= set(body)
 assert body['clock_quality']==clock.SYNCED and isinstance(body['monotonic_time'],float)

async def test_stall_emits_once_with_missed_intervals():
 sync_clock();b=B();a=m.Atom();await a.initialize(b.ctx());a._running=True;event='SYS_SECOND';bucket=int(clock.now());a._last_bucket[event]=bucket-10;await a._emit_tick(event)
 rows=[p for n,p in b.e if n==event];assert len(rows)==1 and rows[0]['missed_intervals']==10
 snap=await a.snapshot();b2=B();a2=m.Atom();await a2.initialize(b2.ctx());await a2.restore(snap);a2._running=True
 await a2._emit_tick(event);assert not [p for n,p in b2.e if n==event]
 snap['last_bucket'][event]-=5;b3=B();a3=m.Atom();await a3.initialize(b3.ctx());await a3.restore(snap);a3._running=True
 await a3._emit_tick(event);resumed=[p for n,p in b3.e if n==event]
 assert len(resumed)==1 and resumed[0]['missed_intervals']==5

async def test_start_idempotent_and_stop_silences_tasks():
 sync_clock();b=B();a=m.Atom();await a.initialize(b.ctx());await a.start();tasks=tuple(a._tasks);await a.start();assert len(a._tasks)==5 and tuple(a._tasks)==tasks
 await a.stop();assert not a._tasks;count=len(b.e);await asyncio.sleep(.05);assert len(b.e)==count

async def test_source_has_no_private_wall_clock_or_offset_copy():
 source=(P(__file__).resolve().parents[1]/'atom.py').read_text('utf8');assert 'time.time' not in source and '_offset_s' not in source

async def main():
 for t in (test_five_cadences_defined,test_emit_has_stable_pulse_id_and_clock_fields,
 test_stall_emits_once_with_missed_intervals,test_start_idempotent_and_stop_silences_tasks,
 test_source_has_no_private_wall_clock_or_offset_copy):await t()
if __name__=='__main__':asyncio.run(main())
