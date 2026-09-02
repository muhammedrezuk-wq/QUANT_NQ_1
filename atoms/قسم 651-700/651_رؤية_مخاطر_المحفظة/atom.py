from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION="2.0.0"
EVENT_IN="platform.account.state"
EVENT_OUT="portfolio.account.tracked"

class Atom(AtomBase):
    def __init__(self): self._context=None; self._running=False; self._accounts={}; self._updates=0
    async def initialize(self,context): self._context=context; context.subscribe(EVENT_IN,self._on_account)
    async def start(self): self._running=True
    async def stop(self): self._running=False
    async def shutdown(self): await self.stop()
    async def _on_account(self,payload):
        if not self._running or self._context is None or not isinstance(payload,dict): return
        account=str(payload.get("account_id") or "")
        if not account:return
        state={"component":"account_tracker","account_id":account,"broker":payload.get("broker"),"server":payload.get("server"),"currency":payload.get("currency"),"margin_mode":payload.get("margin_mode"),"connected":bool(payload.get("connected",True)),"trade_allowed":bool(payload.get("trade_allowed",True)),"expert_allowed":bool(payload.get("expert_allowed",True)),"equity":payload.get("equity"),"balance":payload.get("balance"),"measured_at":payload.get("measured_at"),"status":"TRACKED"}
        self._accounts[account]=state; self._updates+=1; await self._context.publish(EVENT_OUT,state)
    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        return HealthStatus(state=HealthState.HEALTHY if self._accounts else HealthState.DEGRADED,message="accounts=%d"%len(self._accounts),details={"accounts":len(self._accounts),"updates":self._updates})
