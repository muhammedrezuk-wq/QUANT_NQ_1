#!/usr/bin/env python3
"""Measured 30-day storage footprint using real atoms, 10 events/day."""
from __future__ import annotations

import sys
import asyncio,importlib.util,json,sqlite3,sys,tempfile
from pathlib import Path
import yaml
from core.contracts.atom import AtomContext
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.h={}
 def subscribe(self,n,h):self.h.setdefault(n,[]).append(h)
 async def publish(self,n,p):
  for h in list(self.h.get(n,[])):await h(dict(p))
def load(d):
 p=ATOM_ROOT/d/'atom.py';name='size_'+d.split('_')[0];s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def rows(path):
 c=sqlite3.connect(path)
 try:
  tables=[r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
  return sum(c.execute(f'select count(*) from {t}').fetchone()[0] for t in tables)
 finally:c.close()
async def main():
 dirs=['701_مخزن_بيانات_السوق','702_مخزن_الصفقات','703_مخزن_الأوامر','704_مخزن_الخط_الزمني','706_مخزن_النماذج','707_مخزن_القرارات','709_مخزن_المحفظة','712_مخزن_التحليل','713_مخزن_البنية','718_مخزن_السيولة']
 with tempfile.TemporaryDirectory() as td:
  out={}
  for d in dirs:
   m=load(d);cfg=yaml.safe_load((ATOM_ROOT/d/'manifest.yaml').read_text())['config'];path=Path(td)/(d.split('_')[0]+'.db');cfg['db_path']=str(path)
   if 'flush_interval_s' in cfg:cfg['flush_interval_s']=999
   b=B();a=m.Atom();await a.initialize(AtomContext(int(d[:3]),cfg,L(),b.publish,b.subscribe));await a.start()
   for day in range(30):
    for j in range(10):
     i=day*10+j+1;stamp=float(day*86400+j);payload={'symbol':'NQ','account_id':'A','timestamp':stamp,'event_type':'OPENED','source_row_id':i,'ticket':i,'request_id':str(i),'model_name':'m','version':str(i),'data':{'weights':[i]*20},'equity':1000+i,'balance':1000,'open_count':1}
     if d.startswith('702'):event=m.EVENT_TRADE
     elif d.startswith('704'):event='platform.trade_event'
     elif d.startswith('706'):event=m.EVENT_PERSIST_REQUESTED
     elif d.startswith('707'):event=m.EVENT_APPROVED
     else:event=m.EVENT_IN
     await b.publish(event,payload)
   await a.stop();out[d[:3]]={'rows':rows(path),'bytes':path.stat().st_size}
  # 708/716 do not own databases.
  out['708']={'rows':0,'bytes':0,'note':'memory + snapshot, no DB'}
  out['716']={'rows':0,'bytes':0,'note':'operates on configured stores, no DB'}
  print(json.dumps(out,ensure_ascii=False,sort_keys=True))
if __name__=='__main__':asyncio.run(main())
