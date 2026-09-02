from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase,AtomContext,HealthState,HealthStatus

ATOM_VERSION="2.0.0"
EVENT_RISK="risk.account.state"
EVENT_EXPOSURE="risk.exposure.state"
EVENT_PROFIT="risk.profit_limits.state"
EVENT_SESSION="risk.session_limits.state"
EVENT_OUT="risk.unified.state"
class Atom(AtomBase):
    def __init__(self):self._context=None;self._running=False;self._risk={};self._exposure={};self._profit={};self._session={};self._seen=0;self._emitted=0
    async def initialize(self,c):
        self._context=c
        for event,bucket in ((EVENT_RISK,self._risk),(EVENT_EXPOSURE,self._exposure),(EVENT_PROFIT,self._profit),(EVENT_SESSION,self._session)):c.subscribe(event,self._handler(bucket))
    async def start(self):self._running=True
    async def stop(self):self._running=False
    async def shutdown(self):await self.stop()
    def _handler(self,bucket):
        async def handler(p):
            if not self._running or not isinstance(p,dict):return
            account=str(p.get("account_id") or "")
            if not account:return
            bucket[account]=dict(p)
            self._seen+=1
            await self._publish()
        return handler
    async def _publish(self):
        if self._context is None:return
        accounts=sorted(set(self._risk)|set(self._exposure)|set(self._profit)|set(self._session));rows={a:{"risk":self._risk.get(a),"exposure":self._exposure.get(a),"profit_limits":self._profit.get(a),"session_limits":self._session.get(a),"status":(self._risk.get(a) or {}).get("status","UNKNOWN")} for a in accounts};await self._context.publish(EVENT_OUT,{"id":"risk_manager","role":"READ_ONLY_AGGREGATOR","authority":"516","accounts":rows,"count":len(rows)});self._emitted+=1
    async def snapshot(self):return {"version":ATOM_VERSION,"risk":self._risk,"exposure":self._exposure,"profit":self._profit,"session":self._session}
    async def restore(self,state):
        if not isinstance(state,dict):raise ValueError("INVALID_RISK_AGGREGATOR_STATE")
        for name in ("risk","exposure","profit","session"):
            value=state.get(name,{})
            if not isinstance(value,dict):raise ValueError("INVALID_RISK_AGGREGATOR_STATE")
            setattr(self,"_"+name,{str(k):dict(v) for k,v in value.items() if isinstance(v,dict)})
    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        d={"risk_accounts":len(self._risk),"seen":self._seen,"emitted":self._emitted,"authority":"516"}
        if not self._risk:return HealthStatus(state=HealthState.DEGRADED,message="RISK_AUTHORITY_STATE_UNKNOWN",details=d)
        return HealthStatus(state=HealthState.HEALTHY,message="aggregating_516_accounts=%d"%len(self._risk),details=d)
