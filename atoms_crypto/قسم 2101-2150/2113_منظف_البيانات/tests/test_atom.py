import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[4];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));spec=importlib.util.spec_from_file_location('_t113',folder/'atom.py');m=importlib.util.module_from_spec(spec);sys.modules['_t113']=m;spec.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def main():
 b=B();a=m.Atom();await a.initialize(m.AtomContext(113,{},L(),b.publish,b.subscribe));await a.start();p={'account_id':'A','symbol':'NQ','bid':1};await a._on_side('market_data.price_received',p);await a._on_side('market_data.price_received',p);out=[x for n,x in b.e if n==m.EVENT_OUT];assert len(out)==1 and out[0]['side_path_only'] and out[0]['validation_status']=='SIDE_ONLY';print('113 independent side-path cleaner tests passed')
if __name__=='__main__':asyncio.run(main())
