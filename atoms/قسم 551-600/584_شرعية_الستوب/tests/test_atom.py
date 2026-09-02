import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3]; folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('a584',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['a584']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def main():
 b=B();a=m.Atom();await a.initialize(m.AtomContext(584,{'stop_buffer':0,'reward_risk':2},L(),b.publish,b.subscribe));await a.start();await a._on_specs({'symbols':[{'account_id':'A','symbol':'X','point':1,'stops_level':2,'volume_min':.1,'volume_step':.1,'volume_max':10}]});await a._on_built({'request_id':'r','account_id':'A','action':'OPEN','symbol':'X','side':'BUY','volume':1,'reference_price':100,'stop_loss':99,'take_profit':102});p=[p for n,p in b.e if n==m.EVENT_LEGAL][-1];assert p['legality_adjusted'] and p['volume']==.5 and p['stop_loss']==98;print('584 stop legality tests passed')
if __name__=='__main__':asyncio.run(main())
