import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('a586',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['a586']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def main():
 b=B();a=m.Atom();await a.initialize(m.AtomContext(586,{},L(),b.publish,b.subscribe));await a.start();await a._on_order({'request_id':'r','account_id':'A','symbol':'BTCUSD','side':'BUY'});await a._on_result({'request_id':'r','account_id':'A','logical_symbol':'BTCUSD','status':'SYMBOL_UNRESOLVED','approved':False});assert [p for n,p in b.e if n==m.EVENT_REJECTED];print('586 symbol gate tests passed')
if __name__=='__main__':asyncio.run(main())
