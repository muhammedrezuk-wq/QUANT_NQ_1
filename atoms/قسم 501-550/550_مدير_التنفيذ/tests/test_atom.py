import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('t550',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['t550']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def main():
 b=B();a=m.Atom();await a.initialize(m.AtomContext(550,{},L(),b.publish,b.subscribe));await a.start();o={'account_id':'A','request_id':'r','symbol':'NQ','side':'BUY','volume':1};await a._on_final(o);assert a._orders['A\x1fr']['stage']=='DECISION_FINALIZED' and 'sent' not in a._counts;await a._on_bridge_written(o);assert a._orders['A\x1fr']['stage']=='QUEUED_TO_BRIDGE';await a._on_ack(o);assert a._orders['A\x1fr']['stage']=='BROKER_ACKNOWLEDGED';await a._on_trade({**o,'event_type':'OPENED'});assert a._orders['A\x1fr']['stage']=='FILLED_OPEN';await a._on_halt({'account_id':'A','reason':'RISK'});assert a._halted_accounts=={'A':'RISK'};snap=await a.snapshot();c=m.Atom();await c.restore(snap);assert c._counts['decision_finalized']==1;print('550 truthful lifecycle tests passed')
 # ٥٥١ الجديدة (execution.order.skipped) و٥٥٢ (execution.order.rejected بسبب disabled) لازم تنعدّ وتتفصّل بأسبابها — لا رقم أعمى ولا فقدان عند اللقطة.
 d=m.Atom();await d.initialize(m.AtomContext(550,{},L(),b.publish,b.subscribe));await d.start()
 await d._on_skipped({'account_id':'A','request_id':'s1','symbol':'NQ','reason':'NO_SIZE_YET'})
 await d._on_rejected({'account_id':'A','request_id':'r1','symbol':'NQ','reason':'disabled'})
 assert d._counts['order_skipped']==1 and d._counts['rejected']==1
 assert d._skip_reasons=={'NO_SIZE_YET':1} and d._reject_reasons=={'disabled':1}
 snap2=await d.snapshot();e=m.Atom();await e.restore(snap2)
 assert e._skip_reasons=={'NO_SIZE_YET':1} and e._reject_reasons=={'disabled':1},'أسباب التوقّف والرفض لازم تنجو باللقطة'
 print('550 skip/reject reason breakdown tests passed')
if __name__=='__main__':asyncio.run(main())
