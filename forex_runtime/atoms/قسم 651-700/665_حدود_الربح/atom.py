from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_IN="risk.asset_ledger.state";EVENT_OUT="portfolio.profit_limits.state"
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._target=0.;self._updates=0
 async def initialize(self,c):self._context=c;self._target=float(c.config.get("profit_target",0));c.subscribe(EVENT_IN,self._on)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on(self,p):
  if not self._running or self._context is None or not isinstance(p,dict):return
  rows=[]
  for x in p.get("ledgers",[]) if isinstance(p.get("ledgers"),list) else []:
   if isinstance(x,dict):
    g=float(x.get("realized_gross") or 0);rows.append({"account_id":x.get("account_id"),"symbol":x.get("symbol"),"realized_gross":g,"target":self._target,"status":"TARGET_REACHED" if self._target>0 and g>=self._target else "RUNNING","read_only":True})
  self._updates+=1;await self._context.publish(EVENT_OUT,{"assets":rows,"read_only":True,"count":len(rows)})
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  return HealthStatus(state=HealthState.HEALTHY if self._updates else HealthState.DEGRADED,message="profit_limit_updates=%d"%self._updates)
