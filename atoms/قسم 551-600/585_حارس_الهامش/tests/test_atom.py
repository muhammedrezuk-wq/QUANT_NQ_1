import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('a585',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['a585']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def main():
 b=B();a=m.Atom();await a.initialize(m.AtomContext(585,{'margin_buffer_pct':.1},L(),b.publish,b.subscribe));await a.start();await a._on_account({'account_id':'A','broker':'BR','leverage':100});await a._on_truth_free_margin({'account_id':'A','broker':'BR','free_margin':1000});await a._on_truth_equity({'account_id':'A','broker':'BR','equity':1000});await a._on_specs({'symbols':[{'account_id':'A','symbol':'BTCUSD','contract_size':1}]});await a._on_order({'request_id':'r1','account_id':'A','symbol':'BTCUSD','action':'OPEN','side':'BUY','volume':.1,'reference_price':100});p=[p for n,p in b.e if n==m.EVENT_OUT][-1];assert p['approved'];await a._on_truth_free_margin({'account_id':'A','broker':'BR','free_margin':1});await a._on_order({'request_id':'r2','account_id':'A','symbol':'BTCUSD','action':'OPEN','side':'BUY','volume':100,'reference_price':100});p=[p for n,p in b.e if n==m.EVENT_OUT][-1];assert not p['approved'];b2=B();a2=m.Atom();await a2.initialize(m.AtomContext(585,{'margin_buffer_pct':.1},L(),b2.publish,b2.subscribe));await a2.start();await a2._on_account({'account_id':'A','broker':'BR','leverage':100});await a2._on_specs({'symbols':[{'account_id':'A','symbol':'BTCUSD','contract_size':1}]});await a2._on_order({'request_id':'r9','account_id':'A','symbol':'BTCUSD','action':'OPEN','side':'BUY','volume':.1,'reference_price':100});p=[p for n,p in b2.e if n==m.EVENT_OUT][-1];assert not p['approved'] and p['reason']=='FREE_MARGIN_MISSING';sh=[p for n,p in b2.e if n=='financial.truth.shortage'];assert sh and sh[-1]['owner']=='656';print('585 margin guard tests passed (654/656 truth + shortage declared)')
if __name__=='__main__':asyncio.run(main())
