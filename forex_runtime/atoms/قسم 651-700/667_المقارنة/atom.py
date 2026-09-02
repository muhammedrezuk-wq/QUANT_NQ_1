from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_IN="portfolio.performance.state";EVENT_OUT="portfolio.comparison.state"
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._rows={};self._updates=0
 async def initialize(self,c):self._context=c;c.subscribe(EVENT_IN,self._on)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on(self,p):
  if not self._running or self._context is None or not isinstance(p,dict) or not p.get("account_id"):return
  self._rows[str(p["account_id"])] = dict(p);ranking=sorted(self._rows.values(),key=lambda x:float(x.get("realized_profit") or 0),reverse=True);self._updates+=1;await self._context.publish(EVENT_OUT,{"ranking":ranking,"read_only":True,"count":len(ranking)})
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  return HealthStatus(state=HealthState.HEALTHY if self._updates else HealthState.DEGRADED,message="comparison_updates=%d"%self._updates)
