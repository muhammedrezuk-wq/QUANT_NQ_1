from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.0.0"

EVENT_IN="platform.account.state"
EVENT_TERMINAL="platform.terminal_state"
EVENT_OUT="portfolio.account.state"
class Atom(AtomBase):
 def __init__(self):self._context=None;self._running=False;self._last=None;self._updates=0
 async def initialize(self,context):self._context=context;context.subscribe(EVENT_IN,self._on_account);context.subscribe(EVENT_TERMINAL,self._on_terminal)
 async def start(self):self._running=True
 async def stop(self):self._running=False
 async def shutdown(self):await self.stop()
 async def _on_account(self,p):
  if not self._running or self._context is None or not isinstance(p,dict) or not p.get("account_id"):return
  self._last={"component":"account","event_name":EVENT_OUT,"account_id":p.get("account_id"),"status":"CONNECTED" if p.get("connected",True) else "DISCONNECTED","trade_allowed":bool(p.get("trade_allowed",True)),"expert_allowed":bool(p.get("expert_allowed",True)),"currency":p.get("currency"),"margin_mode":p.get("margin_mode")};self._updates+=1;await self._context.publish(EVENT_OUT,self._last)
 async def _on_terminal(self,p):
  if self._running and isinstance(p,dict) and self._last is not None:self._last.update({"connected":bool(p.get("connected",True)),"trade_allowed":bool(p.get("trade_allowed",True)),"expert_allowed":bool(p.get("expert_allowed",True))});await self._context.publish(EVENT_OUT,self._last)
 async def health_check(self):
  if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
  return HealthStatus(state=HealthState.HEALTHY if self._last else HealthState.DEGRADED,message="account_state",details={"updates":self._updates})
