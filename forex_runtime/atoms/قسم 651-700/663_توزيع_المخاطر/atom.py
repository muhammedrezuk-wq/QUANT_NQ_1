from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_IN="risk.asset_ledger.state";EVENT_OUT="portfolio.risk_distribution.state"
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._updates=0
 async def initialize(self,c):self._context=c;c.subscribe(EVENT_IN,self._on)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on(self,p):
  if not self._running or self._context is None or not isinstance(p,dict):return
  assets=[{"account_id":x.get("account_id"),"symbol":x.get("symbol"),"R":x.get("R"),"u":x.get("u"),"loss_exposure":x.get("loss_exposure"),"K":x.get("K"),"X":x.get("X"),"read_only":True} for x in p.get("ledgers",[]) if isinstance(x,dict)]
  self._updates+=1;await self._context.publish(EVENT_OUT,{"assets":assets,"read_only":True,"count":len(assets)})
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  return HealthStatus(state=HealthState.HEALTHY if self._updates else HealthState.DEGRADED,message="risk_updates=%d"%self._updates)
