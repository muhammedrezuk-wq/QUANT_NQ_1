import asyncio, inspect, json, os, sys, tempfile
from pathlib import Path as P
sys.path.insert(0,str(P(__file__).resolve().parents[4]));sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.contracts.atom import AtomContext,HealthState
import importlib.util as I
sp=I.spec_from_file_location('a007',P(__file__).resolve().parents[1]/'atom.py');m=I.module_from_spec(sp);sys.modules['a007']=m;sp.loader.exec_module(m)
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
 def ctx(self,c):return AtomContext(7,c,L(),self.publish,self.subscribe)
def write(p,s):P(p).parent.mkdir(parents=True,exist_ok=True);P(p).write_text(s,encoding='utf8')
def cfg(d,files=None,minimum=1):return {'watched_files':files or [],'watched_dirs':[str(d)],'min_watched_items':minimum}
async def new(c):b=B();a=m.Atom();await a.initialize(b.ctx(c));await a.start();return a,b
async def scan(a,b):await b.publish(m.EVENT_PULSE,{});return await a.health_check()

async def test_baseline_then_detect_modification():
 with tempfile.TemporaryDirectory() as t:
  d=P(t)/'sealed';p=d/'a.py';write(p,'x=1');a,b=await new(cfg(d));assert (await a.health_check()).message=='UNTRUSTED'
  assert (await a.establish_baseline()).state==HealthState.HEALTHY;write(p,'x=2');h=await scan(a,b)
  assert h.state==HealthState.UNHEALTHY and any(v['diff_type']=='modified' for v in b.e[-1][1]['violations'])
async def test_detect_missing_file():
 with tempfile.TemporaryDirectory() as t:
  d=P(t)/'sealed';p=d/'a.py';write(p,'x');write(d/'b.py','y');a,b=await new(cfg(d));await a.establish_baseline();p.unlink();h=await scan(a,b)
  assert h.state==HealthState.UNHEALTHY and 'removed' in h.message
async def test_dir_content_modification_detected():
 with tempfile.TemporaryDirectory() as t:
  d=P(t)/'sealed';p=d/'mod.py';write(p,'x=1');a,b=await new(cfg(d));await a.establish_baseline();write(p,'x=999');assert (await scan(a,b)).state==HealthState.UNHEALTHY
async def test_pycache_and_pyc_ignored():
 with tempfile.TemporaryDirectory() as t:
  d=P(t)/'sealed';p=d/'real.py';write(p,'ok=1');write(d/'__pycache__'/'x.pyc','a');a,b=await new(cfg(d));await a.establish_baseline();write(d/'__pycache__'/'x.pyc','b');assert (await scan(a,b)).state==HealthState.HEALTHY
async def test_dir_added_and_removed_source():
 with tempfile.TemporaryDirectory() as t:
  d=P(t)/'sealed';a1=d/'a.py';write(a1,'a');a,b=await new(cfg(d));await a.establish_baseline();write(d/'b.py','b');h=await scan(a,b);assert h.state==HealthState.UNHEALTHY and 'added' in h.message
async def test_snapshot_restore_preserves_security_baseline():
 with tempfile.TemporaryDirectory() as t:
  d=P(t)/'sealed';p=d/'a.py';write(p,'before');c=cfg(d);a,b=await new(c);await a.establish_baseline();snap=await a.snapshot();write(p,'after');a2,b2=await new(c);await a2.restore(snap);assert (await scan(a2,b2)).state==HealthState.UNHEALTHY
async def test_no_config_reports_unknown():
 b=B();a=m.Atom();await a.initialize(b.ctx({'watched_files':[],'watched_dirs':[]}));await a.start();await b.publish(m.EVENT_PULSE,{});h=await a.health_check();assert h.state==HealthState.UNHEALTHY and h.message=='GUARD_DISABLED'
async def test_health_check_reports_without_scanning_or_publishing():
 with tempfile.TemporaryDirectory() as t:
  d=P(t)/'sealed';write(d/'a.py','a');a,b=await new(cfg(d));await a.establish_baseline();before=len(b.e);h=await a.health_check();assert h.state==HealthState.HEALTHY and len(b.e)==before
async def test_release_baseline_is_loaded_and_verified_on_start():
 with tempfile.TemporaryDirectory() as t:
  d=P(t)/'sealed';write(d/'a.py','a');c=cfg(d);probe,b=await new(c)
  baseline=P(t)/'baseline.json';baseline.write_text(json.dumps({'format':'asmar-integrity-baseline','scope_digest':probe._scope_digest,'items':probe._collect()}),encoding='utf8')
  c['baseline_file']=str(baseline);trusted,b2=await new(c);h=await trusted.health_check();assert h.state==HealthState.HEALTHY and h.details['established']

async def main():
 for t in (test_baseline_then_detect_modification,test_detect_missing_file,test_dir_content_modification_detected,test_pycache_and_pyc_ignored,test_dir_added_and_removed_source,test_snapshot_restore_preserves_security_baseline,test_no_config_reports_unknown,test_health_check_reports_without_scanning_or_publishing,test_release_baseline_is_loaded_and_verified_on_start):await t()
if __name__=='__main__':asyncio.run(main())
