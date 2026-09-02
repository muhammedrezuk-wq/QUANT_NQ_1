import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[4];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));spec=importlib.util.spec_from_file_location('_t114',folder/'atom.py');m=importlib.util.module_from_spec(spec);sys.modules['_t114']=m;spec.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def main():
 b=B();a=m.Atom();await a.initialize(m.AtomContext(114,{},L(),b.publish,b.subscribe));await a.start();await a._on_cleaned({'source_event':'market_data.news_received','validation_status':'SIDE_ONLY','side_path_only':True,'payload':{'headline':'x','timestamp':1}});out=[p for n,p in b.e if n==m.EVENT_OUT][-1];assert out['type']=='news_received' and out['side_path_only'] and out['validation_status']=='SIDE_ONLY';print('114 side-path normalizer tests passed')
if __name__=='__main__':asyncio.run(main())
