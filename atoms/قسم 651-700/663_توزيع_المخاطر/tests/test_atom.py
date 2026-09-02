import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('a663',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['a663']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def main():
 b=B();a=m.Atom();await a.initialize(m.AtomContext(663,{},L(),b.publish,b.subscribe));await a.start();await a._on({'ledgers':[{'account_id':'A','symbol':'X','R':100,'u':.2}]});assert [p for n,p in b.e if n==m.EVENT_OUT][-1]['assets'][0]['R']==100;print('663 risk tests passed')
if __name__=='__main__':asyncio.run(main())
