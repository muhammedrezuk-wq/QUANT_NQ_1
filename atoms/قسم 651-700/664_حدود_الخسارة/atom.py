from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_IN="risk.asset_ledger.state";EVENT_OUT="portfolio.loss_limits.state"
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._max_loss=0.0;self._updates=0
 async def initialize(self,c):self._context=c;self._max_loss=float(c.config.get("max_loss_exposure",0));c.subscribe(EVENT_IN,self._on)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on(self,p):
  if not self._running or self._context is None or not isinstance(p,dict):return
  rows=[]
  for x in p.get("ledgers",[]) if isinstance(p.get("ledgers"),list) else []:
   if isinstance(x,dict):
    loss=float(x.get("loss_exposure") or 0);rows.append({"account_id":x.get("account_id"),"symbol":x.get("symbol"),"loss_exposure":loss,"limit":self._max_loss,"status":"LIMITED" if self._max_loss>0 and loss>=self._max_loss else "OK","read_only":True})
  self._updates+=1;await self._context.publish(EVENT_OUT,{"assets":rows,"read_only":True,"count":len(rows)})
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  return HealthStatus(state=HealthState.HEALTHY if self._updates else HealthState.DEGRADED,message="loss_limit_updates=%d"%self._updates)
