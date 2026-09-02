from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
ATOM_VERSION="1.2.0"
FAIL_CLOSED="RESTORE_FAILED_FAIL_CLOSED"
EVENT_ACCOUNT="platform.account.state"
EVENT_POSITIONS="platform.positions.state"
EVENT_TRADE="market.outcome.realized"
EVENT_OUT="portfolio.pnl.state"
def num(v):
 try:r=float(v)
 except (TypeError,ValueError):return 0.0
 return r if r==r else 0.0
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._accounts={};self._floating={};self._realized={};self._seen=set();self._updates=0;self._restore_error=""
 async def initialize(self,c):self._context=c;c.subscribe(EVENT_ACCOUNT,self._on_account);c.subscribe(EVENT_POSITIONS,self._on_positions);c.subscribe(EVENT_TRADE,self._on_trade)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on_account(self,p):
  if self._running and isinstance(p,dict) and p.get("account_id"):self._accounts[str(p["account_id"])]=p;await self._publish(str(p["account_id"]))
 async def _on_positions(self,p):
  if not self._running or not isinstance(p,dict):return
  by={}
  for row in p.get("positions",[]) if isinstance(p.get("positions"),list) else []:
   if isinstance(row,dict):by[str(row.get("account_id") or p.get("account_id") or "")]=by.get(str(row.get("account_id") or p.get("account_id") or ""),0)+num(row.get("profit"))
  affected=set(by)
  if p.get("complete") is True:
   affected.update(self._floating)
   for a in set(self._floating)-set(by):by[a]=0.0
  elif p.get("account_id") and not by:
   a=str(p.get("account_id"))
   by[a]=0.0
   affected.add(a)
  self._floating.update(by)
  for a in affected:await self._publish(a)
 async def _on_trade(self,p):
  if not self._running or not isinstance(p,dict):return
  event=str(p.get("event_id") or p.get("source_row_id") or "")
  if event and event in self._seen:return
  if event:self._seen.add(event)
  a=str(p.get("account_id") or "")
  self._realized[a]=self._realized.get(a,0)+num(p.get("profit"))
  await self._publish(a)
 async def _publish(self,a):
  if self._context is None or not a:return
  acc=self._accounts.get(a,{})
  floating=self._floating.get(a,0.0)
  realized=self._realized.get(a,0.0)
  self._updates+=1
  await self._context.publish(EVENT_OUT,{"component":"pnl","event_name":EVENT_OUT,"account_id":a,"floating_pnl":floating,"realized_pnl":realized,"net_pnl":floating+realized,"equity":acc.get("equity"),"measured_at":acc.get("measured_at")})
 async def snapshot(self):
  return {"version":ATOM_VERSION,"realized":{str(k):float(v) for k,v in self._realized.items()}}
 async def restore(self,state):
  r=state.get("realized") if isinstance(state,dict) else None
  if not isinstance(r,dict) or not all(isinstance(k,str) and isinstance(v,(int,float)) for k,v in r.items()):
   self._realized={}
   self._restore_error=FAIL_CLOSED
   raise ValueError(FAIL_CLOSED)
  self._realized={k:float(v) for k,v in r.items()}
  self._restore_error=""
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  if self._restore_error:return HealthStatus(state=HealthState.DEGRADED,message=self._restore_error,details={"restore_error":self._restore_error})
  return HealthStatus(state=HealthState.HEALTHY if self._updates else HealthState.DEGRADED,message="pnl_updates=%d"%self._updates)
