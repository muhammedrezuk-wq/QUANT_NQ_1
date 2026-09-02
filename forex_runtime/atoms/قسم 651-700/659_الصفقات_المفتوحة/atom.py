from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.1.2"

EVENT_IN="platform.positions.state";EVENT_OUT="portfolio.open_positions.state"
def num(v):
 try:r=float(v)
 except (TypeError,ValueError):return 0.0
 return r if r==r else 0.0
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._updates=0;self._known_accounts=set()
 async def initialize(self,c):self._context=c;c.subscribe(EVENT_IN,self._on)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on(self,p):
  if not self._running or self._context is None or not isinstance(p,dict):return
  rows=p.get("positions") if isinstance(p.get("positions"),list) else [];accounts={}
  for row in rows:
   if isinstance(row,dict):
    a=str(row.get("account_id") or p.get("account_id") or "");accounts.setdefault(a,[]).append(dict(row))
  if p.get("complete") is True:
   for a in self._known_accounts-set(accounts):accounts[a]=[]
  elif p.get("account_id") and not accounts:accounts[str(p.get("account_id"))]=[]
  self._known_accounts.update(accounts)
  for a,positions in accounts.items():
   by={};volume=0.0;floating=0.0
   for row in positions:
    s=str(row.get("symbol") or "");v=num(row.get("volume"));volume+=abs(v);floating+=num(row.get("profit"));by[s]=by.get(s,0)+abs(v)
   self._updates+=1;await self._context.publish(EVENT_OUT,{"component":"open_positions","event_name":EVENT_OUT,"account_id":a,"open_count":len(positions),"total_volume":volume,"floating_pnl":floating,"by_symbol":by,"positions":positions,"read_only":True})
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  if not self._updates:
   return HealthStatus(state=HealthState.HEALTHY,
                       message="READY_AWAITING_FIRST_OPEN_POSITION | positions_updates=0")
  return HealthStatus(state=HealthState.HEALTHY,message="positions_updates=%d"%self._updates)
