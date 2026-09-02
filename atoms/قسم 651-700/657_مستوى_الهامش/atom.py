from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_IN="platform.account.state";EVENT_OUT="portfolio.margin_level.state"
def num(v):
 try:r=float(v)
 except (TypeError,ValueError):return None
 return r if r==r else None
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._updates=0
 async def initialize(self,c):self._context=c;c.subscribe(EVENT_IN,self._on)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on(self,p):
  if not self._running or self._context is None or not isinstance(p,dict) or not p.get("account_id"):return
  margin=num(p.get("margin"));equity=num(p.get("equity"));level=num(p.get("margin_level"));level=level if level is not None else equity/margin*100 if margin and margin>0 and equity is not None else None
  self._updates+=1;await self._context.publish(EVENT_OUT,{"component":"margin_level","event_name":EVENT_OUT,"account_id":p.get("account_id"),"margin_level":level,"margin":margin,"equity":equity,"measured_at":p.get("measured_at")})
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  return HealthStatus(state=HealthState.HEALTHY if self._updates else HealthState.DEGRADED,message="margin_level_updates=%d"%self._updates)
