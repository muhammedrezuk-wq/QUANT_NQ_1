import asyncio
import importlib.util
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[3]; folder=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root)); sys.path.insert(0,str(folder)); spec=importlib.util.spec_from_file_location("_atom579_final",folder/"atom.py"); mod=importlib.util.module_from_spec(spec); sys.modules["_atom579_final"]=mod; spec.loader.exec_module(mod)
class L:
    def debug(self,*a,**k):pass
    def info(self,*a,**k):pass
    def warning(self,*a,**k):pass
    def error(self,*a,**k):pass
    def critical(self,*a,**k):pass
class B:
    def __init__(self):self.e=[]
    def subscribe(self,*a):pass
    async def publish(self,n,p):self.e.append((n,p))
    def c(self):return mod.AtomContext(579,{"lot_step":.01},L(),self.publish,self.subscribe)
def ev(b,n):return [p for x,p in b.e if x==n]
async def new():
    b=B();a=mod.Atom();await a.initialize(b.c());await a.start();return a,b
async def positions(a):
    await a._on_positions({"positions":[
        {"account_id":"A","symbol":"X","ticket":1,"side":"BUY","volume":1,"profit":100},
        {"account_id":"A","symbol":"X","ticket":2,"side":"SELL","volume":2,"profit":10},
    ]})
async def main():
    a,b=await new();await positions(a)
    await a._on_partial({"extraction_id":"e1","account_id":"A","symbol":"X","amount":50})
    assert ev(b,mod.EVENT_PENDING) and str(ev(b,mod.EVENT_MANAGE)[-1]["ticket"])=="1" and not ev(b,mod.EVENT_CONFIRMED)
    await a._on_trade({"event_type":"PARTIAL","ticket":1,"account_id":"A","symbol":"X","request_id":"e1-1-a1","profit":50})
    assert ev(b,mod.EVENT_CONFIRMED)[-1]["actual_amount"]==50
    print("OK — الأمر لا يُحسب تخريجًا حتى يصل حدث الوسيط الفعلي")

    a2,b2=await new();await a2._on_positions({"positions":[{"account_id":"A","symbol":"X","ticket":9,"side":"BUY","volume":1,"profit":10}]})
    await a2._on_partial({"extraction_id":"e2","account_id":"A","symbol":"X","amount":20})
    assert ev(b2,mod.EVENT_MANAGE)[-1]["volume"]==1.0
    print("OK — الحجم محصور بحجم الرجل الرابحة")

    a3,b3=await new();await a3._on_positions({"positions":[{"account_id":"A","symbol":"X","ticket":9,"side":"BUY","volume":1,"profit":0}]})
    await a3._on_partial({"extraction_id":"e3","account_id":"A","symbol":"X","amount":20})
    assert ev(b3,mod.EVENT_FAILED)
    print("OK — لا يغلق رجلًا خاسرة")

    a4,b4=await new();await positions(a4)
    await a4._on_full({"extraction_id":"full1","account_id":"A","symbol":"X","amount":200})
    assert len(ev(b4,mod.EVENT_MANAGE))==2 and not ev(b4,mod.EVENT_DEACTIVATE)
    await a4._on_trade({"event_type":"CLOSED","ticket":1,"account_id":"A","symbol":"X","request_id":"full1-1-a1","profit":100})
    assert not ev(b4,mod.EVENT_DEACTIVATE)
    await a4._on_trade({"event_type":"CLOSED","ticket":2,"account_id":"A","symbol":"X","request_id":"full1-2-a1","profit":10})
    assert ev(b4,mod.EVENT_CONFIRMED) and ev(b4,mod.EVENT_DEACTIVATE)
    print("OK — التخريج الكامل ينتظر إغلاق كل الأرجل الفعلي قبل التعطيل")

    a5,b5=await new();assert (await a5.health_check()).state==mod.HealthState.HEALTHY
    print("OK — الصحة")
if __name__=="__main__":asyncio.run(main())
