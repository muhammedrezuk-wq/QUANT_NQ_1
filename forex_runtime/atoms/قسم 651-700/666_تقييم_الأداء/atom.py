from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
ATOM_VERSION="1.1.0"
FAIL_CLOSED="RESTORE_FAILED_FAIL_CLOSED"
_KEYS=("trades","wins","losses","profit","peak","drawdown")
EVENT_PNL="portfolio.pnl.state"
EVENT_TRADE="market.outcome.realized"
EVENT_OUT="portfolio.performance.state"
def num(v):
 try:r=float(v)
 except (TypeError,ValueError):return 0.0
 return r if r==r else 0.0
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._stats={};self._seen=set();self._updates=0;self._restore_error=""
 async def initialize(self,c):self._context=c;c.subscribe(EVENT_PNL,self._on_pnl);c.subscribe(EVENT_TRADE,self._on_trade)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 def bucket(self,a):return self._stats.setdefault(a,{"trades":0,"wins":0,"losses":0,"profit":0.,"peak":0.,"drawdown":0.})
 async def _on_pnl(self,p):
  if self._running and isinstance(p,dict) and p.get("account_id"):
   b=self.bucket(str(p["account_id"]))
   equity=num(p.get("equity"))
   b["peak"]=max(b["peak"],equity)
   b["drawdown"]=max(b["drawdown"],b["peak"]-equity)
   await self._publish(str(p["account_id"]))
 async def _on_trade(self,p):
  if not self._running or not isinstance(p,dict):return
  eid=str(p.get("event_id") or p.get("source_row_id") or "");
  if eid and eid in self._seen:return
  if eid:self._seen.add(eid)
  a=str(p.get("account_id") or "")
  profit=num(p.get("profit"))
  b=self.bucket(a)
  b["trades"]+=1
  b["profit"]+=profit
  b["wins"]+=int(profit>0)
  b["losses"]+=int(profit<0)
  await self._publish(a)
 async def _publish(self,a):
  if self._context is None or not a:return
  b=self.bucket(a)
  await self._context.publish(EVENT_OUT,{"account_id":a,"trades":b["trades"],"wins":b["wins"],"losses":b["losses"],"win_rate":b["wins"]/b["trades"] if b["trades"] else 0.,"realized_profit":b["profit"],"max_drawdown":b["drawdown"],"read_only":True})
  self._updates+=1
 async def snapshot(self):
  return {"version":ATOM_VERSION,"stats":{str(a):{k:float(b[k]) for k in _KEYS} for a,b in self._stats.items()}}
 async def restore(self,state):
  s=state.get("stats") if isinstance(state,dict) else None
  ok=isinstance(s,dict) and all(isinstance(a,str) and isinstance(b,dict) and all(k in b and isinstance(b[k],(int,float)) for k in _KEYS) for a,b in s.items())
  if not ok:
   self._stats={}
   self._restore_error=FAIL_CLOSED
   raise ValueError(FAIL_CLOSED)
  self._stats={a:{"trades":int(b["trades"]),"wins":int(b["wins"]),"losses":int(b["losses"]),"profit":float(b["profit"]),"peak":float(b["peak"]),"drawdown":float(b["drawdown"])} for a,b in s.items()}
  self._restore_error=""
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  if self._restore_error:return HealthStatus(state=HealthState.DEGRADED,message=self._restore_error,details={"restore_error":self._restore_error})
  return HealthStatus(state=HealthState.HEALTHY if self._updates else HealthState.DEGRADED,message="performance_updates=%d"%self._updates)
