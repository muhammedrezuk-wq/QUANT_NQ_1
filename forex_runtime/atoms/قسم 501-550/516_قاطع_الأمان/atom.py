from __future__ import annotations

import asyncio
import copy
import math
from typing import Any
from clock import PulseGuard
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_truth import EVENT_SHORTAGE, FinancialTruth, bind_truth
from shared.financial_scope import text
from shared.durable_execution_journal import Journal

ATOM_VERSION = "5.3.0"
# v5.2.0 (2026-08-25): the owner's master reset button reaches the switch.
# Measured live: the dashboard's kill_switch_reset arrives with an EMPTY
# payload, and the release handler required account_id -- the owner's press
# was silently counted as an identity rejection while every downstream
# guard (552/575/578) cleared, leaving the AUTHORITY latched and 551
# skipping all 18 requests. The direction law is asymmetric on purpose:
# a HALT without identity never widens (dangerous direction); an EXPLICIT
# owner release without identity releases every latched book (restoring
# normal operation is the safe direction for his own button).
RISK_EPSILON=1e-9
FINANCIAL_SCOPE_PARTS=3
EVENT_VALIDATE="risk.margin.validation.completed"
EVENT_LOSS="risk.loss_reported"
EVENT_DAY="SYS_DAY"
EVENT_CLOCK="SYS_SECOND"
EVENT_RELEASE_REQUEST="risk.release.requested"
EVENT_RESET="risk.kill_switch.reset_requested"
EVENT_POSITIONS="platform.positions.state"
EVENT_ACCOUNT="platform.account.state"
EVENT_TERMINAL="platform.terminal_state"
EVENT_LEDGER="risk.asset_ledger.state"
EVENT_REJECTED="execution.order.rejected"
EVENT_TRADE="platform.trade_event"
EVENT_VALIDATED="risk.validation.completed"
EVENT_HALT="emergency.halt"
EVENT_HALT_REQUEST="risk.halt.requested"
EVENT_STATE="risk.account.state"
EVENT_AUDIT="risk.audit.state"


def number(v:Any)->float|None:
    if isinstance(v,bool):return None
    try:r=float(v)
    except (TypeError,ValueError):return None
    return r if math.isfinite(r) else None

def opens_new(p):return str(p.get("action") or "OPEN").upper()=="OPEN"

class Atom(AtomBase):
    def __init__(self):
        self._context=None
        self._running=False
        self._max_daily_loss_pct=5.
        self._max_consecutive_losses=3
        self._max_daily_trades=20
        self._max_open_trades=5
        self._max_reserved_risk_pct=100.
        self._books={}
        self._ledger={}
        self._reservations={}
        self._official_time=None
        self._seen=0
        self._validations=0
        self._rejections=0
        self._identity_rejections=0
        self._incomplete_ignored=0
        self._processed_result_ids=set()
        self._journal=None
        self._storage_error=""
        self._duplicates=0
        self._day_guard=PulseGuard(EVENT_DAY)
        self._truth=FinancialTruth('516')
        self._locks={}
    def book(self,a):return self._books.setdefault(a,{"broker":"","daily_loss_pct":0.,"consecutive_losses":0,"daily_trade_count":0,"open_trade_count":0,"kill":False,"reason":"","account_status":"UNKNOWN","system_status":"UNKNOWN","equity":None})
    def _lock(self,a):return self._locks.setdefault(a,asyncio.Lock())
    async def initialize(self,c):
        self._context=c
        cfg=c.config
        self._max_daily_loss_pct=float(cfg["max_daily_loss_pct"])
        self._max_consecutive_losses=int(cfg["max_consecutive_losses"])
        self._max_daily_trades=int(cfg["max_daily_trades"])
        self._max_open_trades=int(cfg["max_open_trades"])
        self._max_reserved_risk_pct=float(cfg.get("max_reserved_risk_pct",100.0))
        self._journal=Journal(str(cfg.get("consumer_db_path") or "var/store/risk_guard_consumer_516.db"))
        try:
            self._journal.ensure()
            for account,state in self._journal.consumer_states("516").items():self.book(account).update(state)
        except Exception as exc:self._storage_error=type(exc).__name__
        for event,handler in ((EVENT_VALIDATE,self._on_validate),(EVENT_LOSS,self._on_loss),(EVENT_DAY,self._on_day),(EVENT_CLOCK,self._on_clock),(EVENT_RELEASE_REQUEST,self._on_reset),(EVENT_HALT_REQUEST,self._on_halt_request),(EVENT_POSITIONS,self._on_positions),(EVENT_ACCOUNT,self._on_account),(EVENT_TERMINAL,self._on_terminal),(EVENT_LEDGER,self._on_ledger),(EVENT_REJECTED,self._release),(EVENT_TRADE,self._release)):c.subscribe(event,handler)
        bind_truth(self,c,self._truth,("equity",),after=self._on_equity)
    async def start(self):self._running=True;await self._flush_outbox()
    async def stop(self):self._running=False
    async def shutdown(self):await self.stop()
    async def _flush_outbox(self):
        if self._context is None or self._journal is None or self._storage_error:return
        try:
            for output_id,event_name,payload in await asyncio.to_thread(self._journal.pending_outputs):
                await self._context.publish(event_name,payload)
                await asyncio.to_thread(self._journal.mark_emitted,output_id)
        except Exception as exc:self._storage_error=type(exc).__name__
    async def _on_clock(self,p):
        if self._running and isinstance(p,dict):
            stamp=number(p.get("official_time"))
            if stamp is not None:self._official_time=stamp
    def _financial_state(self,a):
        b=self.book(a)
        return {key:b[key] for key in ("daily_loss_pct","consecutive_losses","daily_trade_count","kill","reason")}
    async def _persist_financial_state(self,a,event_id):
        if self._journal is None or self._storage_error:return
        try:await asyncio.to_thread(self._journal.save_consumer_state,"516",a,self._financial_state(a),event_id)
        except Exception as exc:self._storage_error=type(exc).__name__
    async def _changed(self,a,reason,before):
        b=self.book(a)
        await self._publish_state(a,reason)
        if self._context is not None and before!=b:
            await self._context.publish(EVENT_AUDIT,{"account_id":a,"broker":b.get("broker"),"reason":reason,"previous":before,"current":copy.deepcopy(b),"timestamp":self._official_time})
    async def _publish_state(self,a,reason=""):
        if self._context is None:return
        b=self.book(a)
        reserved=sum(float(x["amount"]) for x in self._reservations.values() if x["account_id"]==a)
        await self._context.publish(EVENT_STATE,{"account_id":a,"broker":b.get("broker"),"status":"HALTED" if b["kill"] else "READY" if b["account_status"]==b["system_status"]=="HEALTHY" else "UNKNOWN","daily_loss_pct":b["daily_loss_pct"],"consecutive_losses":b["consecutive_losses"],"daily_trade_count":b["daily_trade_count"],"open_trade_count":b["open_trade_count"],"reserved_risk":reserved,"kill_switch_state":b["kill"],"kill_switch_reason":b["reason"],"account_status":b["account_status"],"system_status":b["system_status"],"reason":reason,"timestamp":self._official_time})
    async def _on_account(self,p):
        if not self._running or not isinstance(p,dict):return
        a=text(p.get("account_id"))
        broker=text(p.get("broker"))
        if not a or not broker:self._identity_rejections+=1;return
        equity=self._truth.get(a,"equity")
        before=copy.deepcopy(self.book(a))
        b=self.book(a)
        b["broker"]=broker
        b["equity"]=equity if equity and equity>0 else None
        b["account_status"]="UNKNOWN" if p.get("stale") is True or b["equity"] is None else "HEALTHY"
        await self._changed(a,"ACCOUNT_STATE",before)
        if b["equity"] is None and self._context is not None:
            await self._context.publish(EVENT_SHORTAGE,self._truth.shortage_body(a,"equity",broker=broker,detail="516 safety breaker"))
    async def _on_equity(self,a):
        if not self._running or not a:return
        equity=self._truth.get(a,"equity")
        before=copy.deepcopy(self.book(a))
        b=self.book(a)
        b["equity"]=equity if equity and equity>0 else None
        if b["broker"]:b["account_status"]="HEALTHY" if b["equity"] is not None else "UNKNOWN"
        await self._changed(a,"ACCOUNT_EQUITY",before)
    async def _on_terminal(self,p):
        if not self._running or not isinstance(p,dict):return
        a=text(p.get("account_id"))
        if not a:self._identity_rejections+=1;return
        before=copy.deepcopy(self.book(a))
        known=all(k in p for k in ("connected","trade_allowed","expert_allowed"))
        healthy=known and all(bool(p.get(k)) for k in ("connected","trade_allowed","expert_allowed"))
        self.book(a)["system_status"]="HEALTHY" if healthy else "DEGRADED" if known else "UNKNOWN"
        await self._changed(a,"SYSTEM_STATE",before)
    async def _on_ledger(self,p):
        if not self._running or not isinstance(p,dict):return
        for row in p.get("ledgers",[]) if isinstance(p.get("ledgers"),list) else []:
            if not isinstance(row,dict):continue
            a=text(row.get("account_id"))
            broker=text(row.get("broker")) or self.book(a).get("broker","") if a else ""
            symbol=text(row.get("symbol"))
            budget=number(row.get("risk_budget",row.get("R")))
            used=number(row.get("loss_exposure"))
            if a and broker and symbol and budget is not None and budget>=0:self._ledger[(a,broker,symbol)]={"budget":budget,"used":max(0.,used or 0.)}
    def _reserved(self,scope):return sum(float(x["amount"]) for x in self._reservations.values() if x["scope"]==scope)
    def _reject_reason(self,a,p):
        b=self.book(a)
        # حرج ٥: فشل تخزين = رفض كل الأوامر (fail-closed)
        if self._storage_error:return "RISK_LEDGER_UNAVAILABLE"
        if b["kill"]:return "KILL_SWITCH"
        if b["account_status"]!="HEALTHY":return "ACCOUNT_STATE_UNKNOWN"
        if b["system_status"]!="HEALTHY":return "SYSTEM_STATE_UNKNOWN"
        if b["daily_trade_count"]>=self._max_daily_trades:return "MAX_DAILY_TRADES"
        if b["open_trade_count"]>=self._max_open_trades:return "MAX_OPEN_TRADES"
        if opens_new(p):
            broker=text(p.get("broker")) or b.get("broker","")
            symbol=text(p.get("symbol"))
            amount=number(p.get("risk_budget"))
            if not broker or not symbol:return "MISSING_FINANCIAL_SCOPE"
            if amount is None or amount<=0:return "RISK_BUDGET_UNKNOWN"
            scope=(a,broker,symbol)
            ledger=self._ledger.get(scope)
            if ledger is None:return "RISK_LEDGER_UNKNOWN"
            if ledger["used"]+self._reserved(scope)+amount>ledger["budget"]+RISK_EPSILON:return "RISK_BUDGET_EXCEEDED"
            equity=number(b.get("equity"))
            if equity and self._reserved(scope)+amount>equity*self._max_reserved_risk_pct/100.:return "ACCOUNT_RESERVATION_LIMIT"
        return ""
    async def _on_validate(self,p):
        if not self._running or self._context is None or not isinstance(p,dict):return
        self._seen+=1
        self._validations+=1
        a=text(p.get("account_id"))
        out=dict(p)
        if not a:reason="MISSING_ACCOUNT_ID";approved=False;self._identity_rejections+=1
        elif p.get("approved") is False:reason=text(p.get("reason")) or "UPSTREAM_REJECTED";approved=False
        else:reason=self._reject_reason(a,p);approved=not reason
        if not approved:self._rejections+=1
        elif opens_new(p):
            request=text(p.get("request_id"))
            b=self.book(a)
            broker=text(p.get("broker")) or b.get("broker","")
            amount=number(p.get("risk_budget"))
            symbol=text(p.get("symbol"))
            if request and amount is not None:self._reservations[(a,request)]={"account_id":a,"scope":(a,broker,symbol),"amount":amount}
        out.update({"request_id":text(p.get("request_id")),"account_id":a or None,"broker":text(p.get("broker")) or (self.book(a).get("broker","") if a else ""),"symbol":text(p.get("symbol")),"side":text(p.get("side")),"approved":approved,"reason":reason,"kill_switch_state":self.book(a)["kill"] if a else None})
        await self._context.publish(EVENT_VALIDATED,out)
        if a:await self._publish_state(a,"VALIDATION_"+("APPROVED" if approved else "REJECTED"))
    async def _on_loss(self,p):
        if not self._running or not isinstance(p,dict):return
        event_id=text(p.get("event_id"))
        a=text(p.get("account_id"))
        if not event_id or not a:self._identity_rejections+=1;return
        # حرج ٥: لا نتوقف عند خطأ تخزين — نحسب في الذاكرة دائماً
        completeness=text(p.get("completeness") or ("COMPLETE" if p.get("costs_complete") is True else "UNKNOWN")).upper()
        loss=number(p.get("loss_pct"))
        b=self.book(a)
        initial={key:b[key] for key in ("daily_loss_pct","consecutive_losses","daily_trade_count","kill","reason")}
        def reduce(state):
            outputs=[]
            if loss is None:return state,outputs
            if completeness!="COMPLETE":
                state["incomplete_costs"]=int(state.get("incomplete_costs",0))+1
            state["daily_loss_pct"]=float(state.get("daily_loss_pct",0.))+loss
            state["consecutive_losses"]=int(state.get("consecutive_losses",0))+1 if p.get("is_loss") is True else 0
            state["daily_trade_count"]=int(state.get("daily_trade_count",0))+1
            reason="RISK_DAILY_LIMIT" if state["daily_loss_pct"]>=self._max_daily_loss_pct else "MAX_CONSECUTIVE_LOSSES" if state["consecutive_losses"]>=self._max_consecutive_losses else ""
            if reason and not state.get("kill"):
                state["kill"]=True
                state["reason"]=reason
                outputs.append(("risk-halt:"+event_id,EVENT_HALT,{"account_id":a,"broker":b.get("broker"),"reason":reason,"origin":"516","daily_loss_pct":round(state["daily_loss_pct"],4),"consecutive_losses":state["consecutive_losses"]}))
            return state,outputs
        async with self._lock(a):
            fresh=True
            if self._journal is not None and not self._storage_error:
                try:
                    fresh,state=await asyncio.to_thread(self._journal.reduce_consumer_event,event_id,a,"TRADE_RESULT",event_id,p,"516",a,initial,reduce)
                except Exception as exc:
                    # حرج ٥: فشل تخزين — حساب في الذاكرة + fail-closed
                    self._storage_error=type(exc).__name__
                    _,state=reduce(dict(initial))
                    if not state.get("kill"):
                        state["kill"]=True
                        state["reason"]=state.get("reason") or "RISK_LEDGER_UNAVAILABLE"
            else:
                # حرج ٥: لا journal أو خطأ سابق — حساب في الذاكرة + fail-closed
                _,state=reduce(dict(initial))
                if not state.get("kill") and self._storage_error:
                    state["kill"]=True
                    state["reason"]=state.get("reason") or "RISK_LEDGER_UNAVAILABLE"
            before=copy.deepcopy(b)
            b.update(state)
        if not fresh:self._duplicates+=1;return
        self._processed_result_ids.add(event_id)
        if loss is None:self._incomplete_ignored+=1;await self._publish_state(a,"LOSS_UNKNOWN_IGNORED");return
        await self._flush_outbox()
        await self._changed(a,"COMPLETE_TRADE_RESULT" if completeness=="COMPLETE" else "TRADE_RESULT_COSTS_INCOMPLETE",before)
    async def _trip(self,a,reason,origin="516"):
        if not a or self._context is None:return
        async with self._lock(a):
            b=self.book(a)
            if b["kill"]:return
            before=copy.deepcopy(b)
            b["kill"]=True
            b["reason"]=reason
            await self._persist_financial_state(a,"administrative-halt:"+reason)
        await self._context.publish(EVENT_HALT,{"account_id":a,"broker":b.get("broker"),"reason":reason,"origin":origin,"daily_loss_pct":round(b["daily_loss_pct"],4),"consecutive_losses":b["consecutive_losses"]})
        await self._changed(a,"HARD_STOP",before)
    async def _on_halt_request(self,p):
        if not self._running or not isinstance(p,dict):return
        a=text(p.get("account_id"))
        if not a:self._identity_rejections+=1;return
        await self._trip(a,text(p.get("reason")) or "KILL_SWITCH",text(p.get("origin")) or "516")
    async def _on_day(self,p):
        if not self._running or not self._day_guard.accept(p):return
        for a,b in list(self._books.items()):
            async with self._lock(a):
                before=copy.deepcopy(b)
                b["daily_loss_pct"]=0.
                b["daily_trade_count"]=0
                await self._persist_financial_state(a,"day-reset:"+str(p.get("pulse_id") or p.get("bucket_start") or ""))
            await self._changed(a,"DAY_ROLL_COUNTER_RESET",before)
    async def _on_reset(self,p):
        if not self._running or not isinstance(p,dict):return
        a=text(p.get("account_id"))
        # v5.2.0: an explicit release with no account = the owner's master
        # button -- unlocks every book currently killed.
        targets=[a] if a else [x for x in list(self._books) if self.book(x).get("kill")]
        if not targets:return
        for account in targets:
            async with self._lock(account):
                before=copy.deepcopy(self.book(account))
                b=self.book(account)
                b.update({"kill":False,"reason":"","consecutive_losses":0})
                await self._persist_financial_state(account,"owner-release:"+str(p.get("request_id") or self._official_time or ""))
            await self._changed(account,"EXPLICIT_OWNER_RELEASE",before)
            if self._context is not None:await self._context.publish(EVENT_RESET,{"account_id":account,"broker":b.get("broker"),"reason":"OWNER_RELEASE","origin":"516"})
    async def _on_positions(self,p):
        if not self._running or not isinstance(p,dict):return
        rows=p.get("positions")
        if isinstance(rows,list):
            counts={}
            for row in rows:
                if isinstance(row,dict):
                    a=text(row.get("account_id") or p.get("account_id"))
                    if a:counts[a]=counts.get(a,0)+1
            if p.get("complete") is True:
                for a in self._books:counts.setdefault(a,0)
            for a,count in counts.items():
                before=copy.deepcopy(self.book(a))
                self.book(a)["open_trade_count"]=count
                await self._changed(a,"BROKER_POSITION_SNAPSHOT",before)
    async def _release(self,p):
        if not self._running or not isinstance(p,dict):return
        a=text(p.get("account_id"))
        request=text(p.get("request_id"))
        if a and request and self._reservations.pop((a,request),None) is not None:await self._publish_state(a,"RESERVATION_RELEASED")
    async def snapshot(self):
        return {"version":ATOM_VERSION,"books":copy.deepcopy(self._books),"ledger":[{"scope":list(k),**v} for k,v in self._ledger.items()],"reservations":[{"account_id":a,"request_id":r,"scope":list(v["scope"]),"amount":v["amount"]} for (a,r),v in self._reservations.items()],"official_time":self._official_time,"processed_result_ids":sorted(self._processed_result_ids),"day_guard":self._day_guard.snapshot()}
    async def restore(self,state):
        if not isinstance(state,dict) or not isinstance(state.get("books"),dict):raise ValueError("INVALID_KILL_SWITCH_STATE")
        self._books={str(a):dict(b) for a,b in state["books"].items() if isinstance(b,dict)}
        self._ledger={tuple(x["scope"]):{"budget":float(x["budget"]),"used":float(x["used"])} for x in state.get("ledger",[]) if isinstance(x,dict) and isinstance(x.get("scope"),list) and len(x["scope"])==FINANCIAL_SCOPE_PARTS}
        self._reservations={(text(x.get("account_id")),text(x.get("request_id"))):{"account_id":text(x.get("account_id")),"scope":tuple(x["scope"]),"amount":float(x["amount"])} for x in state.get("reservations",[]) if isinstance(x,dict) and isinstance(x.get("scope"),list) and len(x["scope"])==FINANCIAL_SCOPE_PARTS}
        self._official_time=number(state.get("official_time"))
        self._processed_result_ids={str(x) for x in state.get("processed_result_ids",[])}
        if self._journal is not None and not self._storage_error:
            try:
                for account,durable in self._journal.consumer_states("516").items():self.book(account).update(durable)
            except Exception as exc:self._storage_error=type(exc).__name__
        if state.get("day_guard") is not None:self._day_guard.restore(state["day_guard"])
    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        killed=[a for a,b in self._books.items() if b.get("kill")]
        unknown=[a for a,b in self._books.items() if b.get("account_status")!="HEALTHY" or b.get("system_status")!="HEALTHY"]
        d={"accounts":len(self._books),"killed":killed,"unknown":unknown,"reservations":len(self._reservations),"validations":self._validations,"rejections":self._rejections,"identity_rejections":self._identity_rejections,"incomplete_ignored":self._incomplete_ignored,"duplicates":self._duplicates,"storage_error":self._storage_error}
        if self._storage_error:return HealthStatus(state=HealthState.UNHEALTHY,message="DURABLE_CONSUMER_UNAVAILABLE",details=d)
        if killed:return HealthStatus(state=HealthState.DEGRADED,message="KILL_SWITCH_ACTIVE",details=d)
        if not self._books or unknown:return HealthStatus(state=HealthState.DEGRADED,message="RISK_STATE_UNKNOWN",details=d)
        return HealthStatus(state=HealthState.HEALTHY,message="accounts=%d validations=%d"%(len(self._books),self._validations),details=d)
