from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from reconcile_support import (DEFAULT_ACCOUNT, DEFAULT_BROKER, SRC_SEP, actual_records,
    compare, desired_records, identity, normalize, num, parts, scope, stale, text)

ATOM_VERSION="3.5.0"
# v3.5.0 (2026-08-27, item 15/27 of the 27-atom review): three findings,
# verified against current code first. (1) desired_records had TWO
# compounding shape bugs in reconcile_support.py: the outer batch-vs-
# single-record heuristic silently dropped an explicit "desired" list
# whose items lacked a "legs"/"positions" key (falls back to [payload],
# which has neither either -> every leg vanishes, zero error); and even
# past that, the per-item legs lookup's nested-default form always
# resolved to a list when BOTH keys were merely absent, so the "raw is
# itself one leg" fallback beside it was dead code for that exact case.
# Both fixed together (no live producer hits either: 551, the sole
# producer, always sends "legs" directly, never a "desired" wrapper). (2)
# _persist() ran synchronous file I/O (write+fsync+replace) directly on
# the event loop for every desired/ack event -- now threaded via
# asyncio.to_thread, same shape as 516/578/720. (3) restore() mutated
# self._desired/_actual/_stamps/... through a sequence of field parses
# that could each raise -- a failure partway through left self in a torn
# mix of old and new state. Now builds every field into a local first and
# only commits to self once ALL of them parsed successfully; a failure
# leaves self exactly as it was pre-restore.
# v3.2.0 (2026-08-25): the reconcile gate had never opened once in the
# system's life (measured: 18 orders ever built, 18 rejected, the last 12
# RECONCILIATION_NOT_MATCHED) because 551 writes desired state at BUILD
# time -- desired always led actual, the comparison always screamed, and
# the execution that would have resolved it was the thing being blocked.
# Two-part contract fix: a desired leg with no broker ticket is intent
# (PENDING_OPEN, no ATTENTION); the ack from 601 (ticket + request_id)
# binds the ticket onto its desired leg, so after the fill both sides
# share the ticket identity and MATCH is real. A ticketed leg missing at
# the broker still raises MISSING_AT_BROKER exactly as before.
# v3.1.0 (2026-08-25): feed recovery triggers a reconciliation pass -- the
# recovery signal existed (116 publishes market_data.feed_recovered) and the
# reconciler existed, with no wire between them (measured: 97 recovery events
# in two days, zero operational effect). Same handler as the owner's forced
# reconcile.
EVENT_FEED_RECOVERED="market_data.feed_recovered"
EVENT_DESIRED="execution.desired.state"
EVENT_ACTUAL="platform.positions.state"
EVENT_ACK="execution.command.ack"
EVENT_FORCE="execution.reconcile.requested"
EVENT_ACCOUNT="platform.account.state"
EVENT_OUT="execution.reconcile.state"
VOLUME_TOLERANCE=1e-8
PRICE_TOLERANCE=1e-8

class Atom(AtomBase):
    def __init__(self):
        self._context=None
        self._running=False
        self._path=Path("var/reconciliation/desired.json")
        self._brokers={}
        self._desired={}
        self._actual={}
        self._actual_seen=set()
        self._account_actual_seen=set()
        self._stamps={}
        self._acks={}
        self._load_error=""
        self._write_error=""
        self._comparisons=0
    async def initialize(self,context):
        self._context=context
        self._path=Path(str(context.config.get("state_path",self._path)))
        self._load()
        for event,handler in ((EVENT_DESIRED,self._on_desired),(EVENT_ACTUAL,self._on_actual),(EVENT_ACK,self._on_ack),(EVENT_FORCE,self._on_force),(EVENT_FEED_RECOVERED,self._on_force),(EVENT_ACCOUNT,self._on_account)):context.subscribe(event,handler)
    async def start(self):self._running=True
    async def stop(self):self._running=False
    async def shutdown(self):await self.stop()
    async def _on_account(self,p):
        if self._running and isinstance(p,dict):
            a=text(p.get("account_id"))
            b=text(p.get("broker"))
            if a and b:self._brokers[a]=b
    def _enrich(self,p):
        out=dict(p)
        a=text(out.get("account_id"))
        b=text(out.get("broker")) or self._brokers.get(a,"")
        if a and b:out["broker"]=b
        rows=out.get("positions")
        if isinstance(rows,list):
            out["positions"]=[{**row,"broker":text(row.get("broker")) or self._brokers.get(text(row.get("account_id"),a),b)} if isinstance(row,dict) else row for row in rows]
        return out
    def _load_records(self,records,guard_stale=False,target=None):
        if target is None:target=self._desired
        for raw in records:
            if not isinstance(raw,dict):continue
            a=text(raw.get("account_id"),DEFAULT_ACCOUNT)
            b=text(raw.get("broker"),DEFAULT_BROKER)
            s=text(raw.get("asset_canonical"),text(raw.get("symbol")))
            if not s:continue
            legs=raw.get("legs",[])
            legs=legs if isinstance(legs,list) else []
            record={"account_id":a,"broker":b,"asset_canonical":s,"symbol":s,"version":int(num(raw.get("version",0)) or 0),"stamp":num(raw.get("stamp")),"legs":[normalize(x,a,b,s) for x in legs if isinstance(x,dict)]}
            k=scope(a,s,b)
            # v3.4.0: restore respects the same staleness law -- an older
            # memory snapshot does not trample a newer record already
            # loaded from disk (measured: a hot restart replayed a dead
            # pair's ghost over the record that had already migrated).
            if guard_stale and stale(record,target.get(k)):continue
            target[k]=record
    def _load(self):
        if not self._path.exists():return
        try:
            data=json.loads(self._path.read_text(encoding="utf-8"))
            self._load_records(data.get("desired",[]) if isinstance(data,dict) else [])
        except (OSError,json.JSONDecodeError,TypeError,ValueError) as exc:self._load_error=str(exc)
    def _persist_bytes(self,content):
        try:
            self._path.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp=tempfile.mkstemp(dir=self._path.parent,prefix=".desired.",suffix=".tmp")
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as f:f.write(content);f.flush();os.fsync(f.fileno())
                os.replace(tmp,self._path)
            except BaseException:Path(tmp).unlink(missing_ok=True);raise
        except (OSError,TypeError,ValueError) as exc:self._write_error=str(exc);return False
        self._write_error=""
        return True
    async def _persist(self):
        # v3.5.0: the write+fsync+replace was blocking the event loop on
        # every desired/ack event. The JSON snapshot is built HERE, on the
        # calling coroutine -- it touches self._desired, which must not be
        # read from a worker thread while another coroutine mutates it on
        # the main thread. Only the pure file I/O moves to the thread.
        content=json.dumps({"version":2,"desired":list(self._desired.values())},ensure_ascii=False,sort_keys=True)
        return await asyncio.to_thread(self._persist_bytes,content)
    async def _on_desired(self,payload):
        if not self._running or not isinstance(payload,dict):return
        changed=set()
        for record in desired_records(self._enrich(payload)):
            k=scope(record["account_id"],record["asset_canonical"],record["broker"])
            if stale(record,self._desired.get(k)):continue
            self._desired[k]=record
            changed.add(k)
        if changed:await self._persist()
        for k in changed:await self._publish(k)
    async def _on_actual(self,payload):
        if not self._running or not isinstance(payload,dict):return
        grouped,stamp=actual_records(self._enrich(payload))
        affected=set()
        for source_scope,fresh in grouped.items():
            old_stamp=self._stamps.get(source_scope)
            if stamp is not None and old_stamp is not None and stamp<=old_stamp:continue
            bits=source_scope.split(SRC_SEP,2)
            account=bits[1] if len(bits)>1 else DEFAULT_ACCOUNT
            broker=bits[2] if len(bits)>2 else DEFAULT_BROKER
            owner=(account,broker)
            self._account_actual_seen.add(owner)
            old_scopes={k for k,legs in self._actual.items() if any(x.get("_source_scope")==source_scope for x in legs)}
            for k in old_scopes:self._actual.pop(k,None);self._actual_seen.add(k);affected.add(k)
            for leg in fresh:
                k=scope(leg["account_id"],leg["symbol"],leg["broker"])
                self._actual.setdefault(k,[]).append(leg)
                self._actual_seen.add(k)
                affected.add(k)
            if not fresh:
                for k in self._desired:
                    a,b,_=parts(k)
                    if (a,b)==owner:self._actual_seen.add(k);affected.add(k)
            if stamp is not None:self._stamps[source_scope]=stamp
            if not any(parts(k)[:2]==owner for k in self._actual):
                if self._context is not None:
                    await self._context.publish(EVENT_OUT,{"account_id":account,"broker":broker,"symbol":"*","asset_canonical":"*","status":"MATCH_EMPTY_ACCOUNT","actual_snapshot":True,"items":[],"classification_counts":{},"escalate":False,"warnings":[]})
        for k in affected:await self._publish(k)
    async def _on_ack(self,payload):
        if not self._running or not isinstance(payload,dict):return
        cid=text(payload.get("command_id"),text(payload.get("request_id")))
        if cid:self._acks[cid]=dict(payload)
        enriched=self._enrich(payload)
        s=text(enriched.get("asset_canonical"),text(enriched.get("symbol")))
        a=text(enriched.get("account_id"))
        b=text(enriched.get("broker"))
        ticket=text(payload.get("ticket"))
        changed=set()
        if cid and ticket:
            bound=False
            for k,record in self._desired.items():
                for leg in record.get("legs",[]):
                    if not text(leg.get("ticket")) and cid in (text(leg.get("leg_id")),text(leg.get("request_id"))):
                        leg["ticket"]=ticket;leg["_identity"]=identity(leg);changed.add(k);bound=True
            # v3.4.0: an executed-open ack with no registered desired leg
            # is real -- it cleared every gate and its intent record was
            # simply lost (measured: a dead pair's record was shadowing
            # the newer ones). Adopted as a confirmed desired record keyed
            # by its own ticket, so the executed position is never counted
            # "extra" and the symbol never gets stuck.
            if (not bound and a and b and s
                    and str(payload.get("action") or "").upper()=="OPEN"
                    and str(payload.get("status") or "").upper() in ("","DONE")):
                k=scope(a,s,b)
                record=self._desired.get(k)
                if record is None:
                    record=self._desired[k]={"account_id":a,"broker":b,
                        "asset_canonical":s,"symbol":s,"version":1,
                        "stamp":None,"legs":[]}
                if not any(text(x.get("ticket"))==ticket for x in record["legs"]):
                    leg=normalize({"request_id":cid,"leg_id":cid,"ticket":ticket,
                                   "side":text(payload.get("side")),
                                   "volume":payload.get("volume"),
                                   "action":"OPEN"},a,b,s)
                    record["legs"].append(leg)
                    record["version"]=int(num(record.get("version")) or 0)+1
                    changed.add(k)
        if changed:
            await self._persist()
            for k in changed:await self._publish(k)
        if a and b and s:await self._publish(scope(a,s,b))
    async def _on_force(self,p):
        if not self._running or not isinstance(p,dict):return
        p=self._enrich(p)
        a=text(p.get("account_id"))
        b=text(p.get("broker"))
        s=text(p.get("symbol"))
        if a and b and s:
            await self._publish(scope(a,s,b))
            return
        # v3.3.0: a feed-recovery signal (116) arrives with no scope -- the
        # wire was connected in name only (measured: 97 recovery events,
        # zero passes). Scopeless recovery means a full reconcile sweep
        # over every known scope.
        for k in set(self._desired) | set(self._actual):
            await self._publish(k)
    async def _publish(self,k):
        if self._context is None:return
        state=compare(k,self._desired.get(k),self._actual.get(k,[]),k in self._actual_seen,VOLUME_TOLERANCE,PRICE_TOLERANCE,len(self._acks))
        self._comparisons+=1
        await self._context.publish(EVENT_OUT,state)
    def state(self,k):return compare(k,self._desired.get(k),self._actual.get(k,[]),k in self._actual_seen,VOLUME_TOLERANCE,PRICE_TOLERANCE,len(self._acks))
    async def snapshot(self):
        return {"version":ATOM_VERSION,"desired":list(self._desired.values()),"actual":[{"scope":k,"legs":v} for k,v in self._actual.items()],"actual_seen":sorted(self._actual_seen),"account_actual_seen":[list(x) for x in self._account_actual_seen],"stamps":dict(self._stamps),"acks":dict(self._acks),"brokers":dict(self._brokers)}
    async def restore(self,state):
        if not isinstance(state,dict) or not isinstance(state.get("desired"),list):raise ValueError("INVALID_RECONCILIATION_STATE")
        # v3.5.0: every field is parsed into a LOCAL first -- self is only
        # touched once ALL of them succeed. A parse failure partway through
        # (a corrupt stamp, a malformed account_actual_seen entry, ...)
        # must leave self exactly as it was before restore(), not a torn
        # mix of some fields already replaced and others still stale/old.
        new_desired=dict(self._desired)
        self._load_records(state["desired"],guard_stale=True,target=new_desired)
        new_actual={str(x["scope"]):list(x["legs"]) for x in state.get("actual",[]) if isinstance(x,dict) and isinstance(x.get("legs"),list)}
        new_actual_seen={str(x) for x in state.get("actual_seen",[])}
        new_account_actual_seen={tuple(x) for x in state.get("account_actual_seen",[]) if isinstance(x,list) and len(x)==2}
        new_stamps={str(k):float(v) for k,v in (state.get("stamps") or {}).items()}
        new_acks={str(k):dict(v) for k,v in (state.get("acks") or {}).items() if isinstance(v,dict)}
        new_brokers={str(k):str(v) for k,v in (state.get("brokers") or {}).items()}
        self._desired=new_desired
        self._actual=new_actual
        self._actual_seen=new_actual_seen
        self._account_actual_seen=new_account_actual_seen
        self._stamps=new_stamps
        self._acks=new_acks
        self._brokers=new_brokers
        await self._persist()
    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        d={"desired":len(self._desired),"actual":len(self._actual),"actual_accounts":len(self._account_actual_seen),"comparisons":self._comparisons,"load_error":self._load_error,"write_error":self._write_error}
        if self._load_error or self._write_error:return HealthStatus(state=HealthState.DEGRADED,message="STATE_STORAGE_ERROR",details=d)
        if not self._account_actual_seen:return HealthStatus(state=HealthState.DEGRADED,message="NO_ACTUAL_SNAPSHOT",details=d)
        return HealthStatus(state=HealthState.HEALTHY,message="reconciliation_live",details=d)
