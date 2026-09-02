import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));spec=importlib.util.spec_from_file_location('_t112',folder/'atom.py');m=importlib.util.module_from_spec(spec);sys.modules['_t112']=m;spec.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def main():
 b=B();a=m.Atom();await a.initialize(m.AtomContext(112,{},L(),b.publish,b.subscribe));await a.start();await a._on_account({'account_id':'A','broker':'BR'});raw={'account_id':'A','symbol':'NQ','provider':'MT5','bid':100,'ask':101,'price':100.5,'volume':1,'timestamp':1,'exchange_timestamp':1,'received_at':1};await a._on_tick(raw);valid=[p for n,p in b.e if n==m.EVENT_VALID][-1];assert all(valid.get(k)==raw.get(k) for k in m.CONTRACT_FIELDS);await a._on_tick({**raw,'bid':float('nan')});assert [p for n,p in b.e if n==m.EVENT_INVALID];await a.stop();assert [p for n,p in b.e if n==m.EVENT_STATE][-1]['status']=='STOPPED';print('112 direct validation gate tests passed')
if __name__=='__main__':asyncio.run(main())
