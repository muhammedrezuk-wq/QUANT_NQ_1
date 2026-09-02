from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.0"

EVENT_IN="portfolio.components.state";EVENT_OUT="portfolio.capital_distribution.state"
def num(v):
 try:r=float(v)
 except (TypeError,ValueError):return 0.0
 return r if r==r else 0.0
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._pct=1.0;self._updates=0;self._last_allocations=None
 async def initialize(self,c):self._context=c;self._pct=float(c.config.get("allocation_pct",1.0));c.subscribe(EVENT_IN,self._on)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on(self,p):
  if not self._running or self._context is None or not isinstance(p,dict):return
  rows=[]
  for account in p.get("accounts",[]) if isinstance(p.get("accounts"),list) else []:
   if isinstance(account,dict):rows.append({"account_id":account.get("account_id"),"equity":account.get("equity"),"balance":account.get("balance"),"allocation_pct":self._pct,"allocated_capital":num(account.get("equity"))*self._pct,"read_only":True})
  # Contract 90-11 point 6 (2026-08-19): 662 must dedupe on its own, not rely
  # solely on 650 already being disciplined -- own defense, not inherited.
  if rows==self._last_allocations:return
  self._last_allocations=rows;self._updates+=1
  await self._context.publish(EVENT_OUT,{"allocations":rows,"read_only":True,"count":len(rows),
      "sequence":p.get("sequence"),"pulse_id":p.get("pulse_id")})
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  return HealthStatus(state=HealthState.HEALTHY if self._updates else HealthState.DEGRADED,message="capital_updates=%d"%self._updates)
