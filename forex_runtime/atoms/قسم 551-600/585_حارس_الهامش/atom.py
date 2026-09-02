from __future__ import annotations
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_truth import FinancialTruth, bind_truth

ATOM_VERSION="1.3.3"
EVENT_ORDER="execution.order.resolved"
EVENT_ACCOUNT="platform.account.state"
EVENT_SPECS="market.symbol_specs"
EVENT_OUT="risk.margin.validation.completed"
EVENT_VERDICT="risk.validation.completed"
EVENT_REJECTED="execution.order.rejected"
EVENT_CMD_FAILED="execution.command.failed"
EVENT_TRADE="platform.trade_event"
EVENT_SHORTAGE="financial.truth.shortage"
OPEN="OPEN"

def num(v:Any)->float|None:
    try:r=float(v)
    except (TypeError,ValueError):return None
    return r if r==r else None

def key(a,s):return str(a or "")+"\x1f"+str(s or "")

class Atom(AtomBase):
    def __init__(self):
        self._context=None
        self._running=False
        self._accounts={}
        self._specs={}
        self._reserved={}
        self._holds={}
        self._buffer=.10
        self._truth=FinancialTruth("585")
        self._seen=0
        self._approved=0
        self._rejected=0
        self._released=0
    async def initialize(self,context):
        self._context=context
        cfg=context.config
        self._buffer=float(cfg.get("margin_buffer_pct",.10))
        context.subscribe(EVENT_ORDER,self._on_order)
        context.subscribe(EVENT_ACCOUNT,self._on_account)
        context.subscribe(EVENT_SPECS,self._on_specs)
        bind_truth(self,context,self._truth,("equity","free_margin"))
        context.subscribe(EVENT_VERDICT,self._on_downstream)
        context.subscribe(EVENT_REJECTED,self._on_downstream)
        context.subscribe(EVENT_CMD_FAILED,self._on_downstream)
        context.subscribe(EVENT_TRADE,self._on_trade)
    async def start(self):self._running=True
    async def stop(self):self._running=False
    async def shutdown(self):await self.stop()
    async def _on_account(self,p):
        if not self._running or not isinstance(p,dict) or not p.get("account_id"):return
        account_id=str(p["account_id"])
        self._accounts[account_id]={"leverage":num(p.get("leverage")),"stale":bool(p.get("stale")),"age_s":num(p.get("age_s")),"broker":str(p.get("broker") or "")}
        self._reserved[account_id]=sum(amount for acc,amount in self._holds.values() if acc==account_id)
    async def _on_specs(self,p):
        if not self._running or not isinstance(p,dict):return
        for row in p.get("symbols",[]) if isinstance(p.get("symbols"),list) else []:
            if isinstance(row,dict) and row.get("symbol"):self._specs[key(row.get("account_id") or p.get("account_id"), row["symbol"])] = dict(row)
    def _required(self,o,account):
        s=o.get("symbol_spec") if isinstance(o.get("symbol_spec"),dict) else self._specs.get(key(o.get("account_id"), o.get("symbol")),{})
        v=num(o.get("volume"))
        price=num(o.get("reference_price"))
        if v is None or v<=0:return None,False
        direct=num(s.get("margin_initial")) or num(s.get("margin_per_volume"))
        if direct is not None:return v*direct,False
        cs=num(s.get("contract_size"))
        lev=num(account.get("leverage"))
        if price is None or price<=0 or cs is None or cs<=0 or lev is None or lev<=0:return None,True
        return v*cs*price/lev,True
    async def _on_order(self,p):
        if not self._running or self._context is None or not isinstance(p,dict):return
        self._seen+=1
        action=str(p.get("action") or OPEN).upper()
        account_id=str(p.get("account_id") or "")
        account=self._accounts.get(account_id)
        reason=""
        required=None
        estimated=False
        rid=str(p.get("request_id") or "")
        if action==OPEN and (not rid or rid in self._holds):approved=False;reason="MISSING_OR_DUPLICATE_REQUEST_ID"
        elif action!=OPEN:approved=True;reason="MANAGEMENT_NO_NEW_MARGIN"
        elif account is None:approved=False;reason="ACCOUNT_MARGIN_DATA_MISSING"
        elif account.get("stale"):approved=False;reason="ACCOUNT_STATE_STALE"
        elif not self._truth.has(account_id,"free_margin"):
            approved=False
            reason="FREE_MARGIN_MISSING"
            await self._context.publish(EVENT_SHORTAGE,self._truth.shortage_body(
                account_id,"free_margin",broker=account.get("broker",""),detail="585 margin gate"))
        else:
            required,estimated=self._required(p,account)
            if required is None:approved=False;reason="MARGIN_PER_VOLUME_MISSING"
            else:
                reserved=self._reserved.get(account_id,0.0)
                free_margin=self._truth.get(account_id,"free_margin")
                available=free_margin-reserved
                need=required*(1.0+self._buffer)
                approved=available>=need
                reason="" if approved else "INSUFFICIENT_FREE_MARGIN"
                if approved:
                    self._reserved[account_id]=reserved+required
                    self._holds[rid]=(account_id,required)
        if approved:self._approved+=1
        else:self._rejected+=1
        await self._context.publish(EVENT_OUT,{**p,"approved":approved,"reason":reason,"required_margin":required,"margin_estimated":estimated,"free_margin":self._truth.get(account_id,"free_margin"),"equity":self._truth.get(account_id,"equity"),"financial_truth_owner":"656","reserved_margin":self._reserved.get(account_id,0.0),"account_age_s":account.get("age_s") if account else None,"account_stale":bool(account.get("stale")) if account else None})
    def _release(self,rid):
        hold=self._holds.pop(str(rid or ""),None)
        if hold is None:return
        account_id,amount=hold
        self._reserved[account_id]=max(0.0,self._reserved.get(account_id,0.0)-amount)
        self._released+=1
    async def _on_downstream(self,p):
        if not self._running or not isinstance(p,dict):return
        if p.get("approved") is False or "approved" not in p:self._release(p.get("request_id"))
    async def _on_trade(self,p):
        if not self._running or not isinstance(p,dict):return
        if str(p.get("event_type") or "").upper() in ("OPENED","REJECTED"):self._release(p.get("request_id"))
    async def snapshot(self):
        return {"version":ATOM_VERSION,"holds":{rid:[acc,amount] for rid,(acc,amount) in self._holds.items()}}
    async def restore(self,state):
        rows=state.get("holds") if isinstance(state,dict) else None
        if not isinstance(rows,dict):
            raise ValueError("INVALID_MARGIN_HOLDS")
        self._holds={}
        for rid,value in rows.items():
            if isinstance(rid,str) and isinstance(value,list) and len(value)==2 and num(value[1]) is not None and num(value[1])>=0:
                self._holds[rid]=(str(value[0]),float(value[1]))
        self._reserved={}
        for account,amount in self._holds.values():self._reserved[account]=self._reserved.get(account,0.0)+amount
    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        d={"seen":self._seen,"approved":self._approved,"rejected":self._rejected,"released":self._released,"held":len(self._holds),"accounts":len(self._accounts),"specs":len(self._specs)}
        if not self._seen:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_ORDER_MARGIN_CHECK | approved=0 rejected=0 released=0",details=d)
        return HealthStatus(state=HealthState.HEALTHY,message="approved=%d rejected=%d released=%d"%(self._approved,self._rejected,self._released),details=d)
