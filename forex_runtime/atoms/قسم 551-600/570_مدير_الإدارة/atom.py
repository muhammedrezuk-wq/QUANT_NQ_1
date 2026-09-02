from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "3.0.2"
EVENT_INTENT="execution.manage.intent"
EVENT_VANISH="platform.position.vanished"
EVENT_OUT="execution.manage.command"
EVENT_WRITTEN="execution.manage.written"
EVENT_FAILED="execution.command.failed"
EVENT_ACK="execution.command.ack"
EVENT_PULSE="SYS_SECOND"
ACTIONS=("MODIFY_SL","CLOSE_PARTIAL","CLOSE")
BUY="BUY"
SELL="SELL"
MAX_RETRY_ATTEMPTS=3

def number(v:Any)->float|None:
    try:r=float(v)
    except (TypeError,ValueError):return None
    return r if r==r else None

class Atom(AtomBase):
    def __init__(self):self._context=None;self._running=False;self._state={};self._pending={};self._seen=0;self._commands=0;self._dropped=0;self._counter=0;self._now=0.;self._failed=0;self._magic=20260801
    async def initialize(self,c):
        self._context=c
        self._magic=int(c.config.get("magic",20260801))
        c.subscribe(EVENT_INTENT,self._on_intent)
        c.subscribe(EVENT_VANISH,self._on_vanish)
        c.subscribe(EVENT_WRITTEN,self._on_written)
        c.subscribe(EVENT_FAILED,self._on_failed)
        c.subscribe(EVENT_ACK,self._on_ack)
        c.subscribe(EVENT_PULSE,self._on_pulse)
    async def start(self):self._running=True
    async def stop(self):self._running=False
    async def shutdown(self):await self.stop()
    async def _on_vanish(self,p):
        if not self._running or not isinstance(p,dict):return
        ticket=str(p.get("ticket") or "")
        account=str(p.get("account_id") or "")
        key=account+"|"+ticket
        self._state.pop(key,None)
        for rid,row in list(self._pending.items()):
            if row["key"]==key:self._pending.pop(rid,None)
    def _st(self,key,side):return self._state.setdefault(key,{"side":side,"best_sl":None,"partialed":False,"closed":False})
    async def _send(self,rid,row):
        if self._context is None:return
        row["attempts"]+=1
        row["last_at"]=self._now
        row["status"]="EMITTED"
        self._commands+=1
        await self._context.publish(EVENT_OUT,dict(row["command"],request_id=rid,attempt=row["attempts"]))
    async def _on_intent(self,p):
        if not self._running or self._context is None or not isinstance(p,dict):return
        self._seen+=1
        ticket=p.get("ticket")
        action=str(p.get("action") or "")
        side=str(p.get("side") or "")
        account=str(p.get("account_id") or "")
        if ticket in (None,"",0) or not account or side not in (BUY,SELL) or action not in ACTIONS:self._dropped+=1;return
        key=account+"|"+str(ticket)
        st=self._st(key,side)
        if st["closed"]:self._dropped+=1;return
        signature=(key,action)
        if any(row["signature"]==signature for row in self._pending.values()):return
        command={"account_id":account,"magic":self._magic,"ticket":ticket,"symbol":str(p.get("symbol") or ""),"side":side,"action":action,"reason":str(p.get("reason") or "")}
        if action=="MODIFY_SL":
            value=number(p.get("stop_loss"))
            best=st["best_sl"]
            if value is None or value<=0 or (best is not None and not ((side==BUY and value>best) or (side==SELL and value<best))):self._dropped+=1;return
            command["stop_loss"]=value
        elif action=="CLOSE_PARTIAL":
            value=number(p.get("volume"))
            if value is None or value<=0 or st["partialed"]:self._dropped+=1;return
            command["volume"]=value
        self._counter+=1
        rid=str(p.get("request_id") or "manage-%s-%s-%d"%(account,ticket,self._counter))
        row={"key":key,"signature":signature,"action":action,"side":side,"command":command,"attempts":0,"last_at":0.,"status":"NEW"}
        self._pending[rid]=row
        await self._send(rid,row)
    async def _on_written(self,p):
        if not self._running or not isinstance(p,dict):return
        rid=str(p.get("request_id") or "")
        row=self._pending.get(rid)
        if row is not None:row["status"]="QUEUED_TO_BRIDGE"
    async def _on_ack(self,p):
        if not self._running or not isinstance(p,dict):return
        rid=str(p.get("request_id") or p.get("command_id") or "")
        row=self._pending.pop(rid,None)
        if row is None:return
        st=self._st(row["key"],row["side"])
        action=row["action"]
        if action=="MODIFY_SL":st["best_sl"]=row["command"]["stop_loss"]
        elif action=="CLOSE_PARTIAL":st["partialed"]=True
        elif action=="CLOSE":st["closed"]=True
    async def _on_failed(self,p):
        if not self._running or not isinstance(p,dict):return
        rid=str(p.get("request_id") or "")
        row=self._pending.get(rid)
        if row is None:return
        row["status"]="RETRY"
        self._failed+=1
    async def _on_pulse(self,p):
        if not self._running or not isinstance(p,dict):return
        try:self._now=float(p.get("official_time"))
        except (TypeError,ValueError):return
        for rid,row in list(self._pending.items()):
            if row["status"]=="RETRY" and row["attempts"]<MAX_RETRY_ATTEMPTS and self._now-row["last_at"]>=2**row["attempts"]:await self._send(rid,row)
            elif row["status"]=="RETRY" and row["attempts"]>=MAX_RETRY_ATTEMPTS:self._pending.pop(rid,None);self._dropped+=1
    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        d={"tracked":len(self._state),"pending":len(self._pending),"seen":self._seen,"commands_emitted":self._commands,"failed":self._failed,"dropped":self._dropped}
        if any(r["status"]=="RETRY" for r in self._pending.values()):
            return HealthStatus(state=HealthState.DEGRADED,message="commands_emitted=%d pending=%d"%(self._commands,len(self._pending)),details=d)
        if not self._seen:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_MANAGE_INTENT | commands_emitted=0 pending=0",details=d)
        return HealthStatus(state=HealthState.HEALTHY,message="commands_emitted=%d pending=%d"%(self._commands,len(self._pending)),details=d)
