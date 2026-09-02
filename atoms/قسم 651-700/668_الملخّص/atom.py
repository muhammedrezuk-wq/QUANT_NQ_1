from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.2.0"

EVENT_COMPONENTS="portfolio.components.state"
EVENTS=(EVENT_COMPONENTS,"portfolio.capital_distribution.state","portfolio.risk_distribution.state","portfolio.loss_limits.state","portfolio.profit_limits.state","portfolio.performance.state","portfolio.comparison.state")
EVENT_OUT="portfolio.overview.state"
EVENT_PULSE="SYS_SECOND"
# Fields that change on every upstream publish regardless of real content
# (own pulse epoch stamps) -- must be excluded from the dedupe comparison or
# every pulse would look like a change. Contract 90-11 point 6, 2026-08-19.
_VOLATILE_KEYS=("sequence","pulse_id")

def _stable(payload):
 return {k:v for k,v in payload.items() if k not in _VOLATILE_KEYS}

class Atom(AtomBase):
 def __init__(self):
  self._context=None;self._running=False;self._parts={};self._updates=0
  self._dirty=False;self._last_snapshot=None
 async def initialize(self,c):self._context=c;c.subscribe(EVENT_PULSE,self._on_pulse)
 async def start(self):
  self._running=True
  for e in EVENTS:self._context.subscribe(e,lambda p,e=e:self._on_named(e,p))
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on_named(self,event,p):
  if not self._running or self._context is None or not isinstance(p,dict):return
  self._parts[event]=dict(p);self._dirty=True
 async def _on_pulse(self,payload):
  if not self._running or self._context is None or not isinstance(payload,dict):return
  if not self._dirty:return
  self._dirty=False
  base=self._parts.get(EVENT_COMPONENTS) or {}
  rows=[dict(r) for r in base.get("accounts",[]) if isinstance(r,dict) and r.get("account_id")]
  snapshot={"components":{e:_stable(p) for e,p in self._parts.items()},"accounts":rows,
   "count_accounts":len(rows),"read_only":True}
  if snapshot==self._last_snapshot:return
  self._last_snapshot=snapshot;self._updates+=1
  await self._context.publish(EVENT_OUT,{"components":self._parts,"accounts":rows,
   "count_accounts":len(rows),"read_only":True,"updates":self._updates,
   "sequence":payload.get("sequence"),"pulse_id":payload.get("pulse_id")})
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  return HealthStatus(state=HealthState.HEALTHY if self._parts else HealthState.DEGRADED,message="overview_parts=%d"%len(self._parts),details={"parts":len(self._parts),"updates":self._updates})
