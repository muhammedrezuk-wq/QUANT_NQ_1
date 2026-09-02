import asyncio
import importlib.util
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[3]; folder=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root)); spec=importlib.util.spec_from_file_location("_atom512_work",folder/"atom.py")
mod=importlib.util.module_from_spec(spec); sys.modules["_atom512_work"]=mod; spec.loader.exec_module(mod)
class Log:
    def debug(self,*a,**k): pass
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def error(self,*a,**k): pass
    def critical(self,*a,**k): pass
class Bus:
    def __init__(self): self.events=[]
    def subscribe(self,*a): pass
    async def publish(self,n,p): self.events.append((n,p))
    def ctx(self): return mod.AtomContext(512,{"price_decimals":8},Log(),self.publish,self.subscribe)
async def main():
    b=Bus(); a=mod.Atom(); await a.initialize(b.ctx()); await a.start()
    await a._on_ledger({"account_id":"A","symbol":"NQ","R":50,"K":20,"cost":0,"v_net":1,"w":100,"vpu":20})
    p=[p for n,p in b.events if n==mod.EVENT_OUT][-1]
    assert p["status"]=="READY" and p["stop_price"]==96.5
    await a._on_ledger({"account_id":"A","symbol":"NQ","R":50,"K":20,"cost":0,"v_net":0,"w":0,"vpu":20})
    assert [p for n,p in b.events if n==mod.EVENT_OUT][-1]["status"]=="HEDGED"
    print("512 P0 tests passed")
if __name__=="__main__": asyncio.run(main())
