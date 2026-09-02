from __future__ import annotations
import math
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "1.5.2"
EVENT_BUILT = "execution.order.built"
EVENT_SPECS = "market.symbol_specs"
EVENT_LEGAL = "execution.order.legal"
EVENT_REJECTED = "execution.order.rejected"
OPEN = "OPEN"
BUY = "BUY"
SELL = "SELL"
EPS = 1e-12
DEFAULT_STEP = 0.01


def num(v: Any) -> float | None:
    try: r = float(v)
    except (TypeError, ValueError): return None
    return r if r == r else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context=None
        self._running=False
        self._specs={}
        self._buffer=0.0
        self._reward=2.0
        self._seen=0
        self._legal=0
        self._rejected=0
        self._hard_floor_points=0.0

    async def initialize(self, context: AtomContext) -> None:
        self._context=context
        cfg=context.config
        self._buffer=float(cfg.get("stop_buffer",0.0))
        self._reward=float(cfg.get("reward_risk",2.0))
        self._hard_floor_points=float(cfg.get("hard_floor_points",0.0))
        context.subscribe(EVENT_BUILT,self._on_built)
        context.subscribe(EVENT_SPECS,self._on_specs)
    async def start(self): self._running=True
    async def stop(self): self._running=False
    async def shutdown(self): await self.stop()

    async def _on_specs(self,payload):
        if not self._running or not isinstance(payload,dict): return
        for row in payload.get("symbols",[]) if isinstance(payload.get("symbols"),list) else []:
            if isinstance(row,dict) and row.get("symbol"):
                account = str(row.get("account_id") or payload.get("account_id") or "")
                if account: self._specs[account + "\x1f" + str(row["symbol"])] = dict(row)

    def _neutral(self,o):
        return str(o.get("action") or OPEN).upper()==OPEN and str(o.get("protection_mode") or "").upper()=="NEUTRAL_HEDGE" and o.get("pair_required") is True and bool(o.get("pair_id"))

    def _perpetual(self,o):
        return (str(o.get("action") or OPEN).upper()==OPEN
                and str(o.get("protection_mode") or "").upper()=="PERPETUAL_BUDGET"
                and str(o.get("origin") or "")=="perpetual-delta"
                and (num(o.get("risk_budget")) or 0.0)>0.0
                and (num(o.get("stop_loss")) or 0.0)>0.0
                and o.get("take_profit") in (None,0,0.0))

    def _round_volume(self, volume, spec):
        step=num(spec.get("volume_step")) or DEFAULT_STEP
        minimum=num(spec.get("volume_min")) or step
        maximum=num(spec.get("volume_max"))
        value=math.floor(max(0.0,volume)/step+EPS)*step
        if value + EPS < minimum: return 0.0
        return min(maximum,value) if maximum else value

    async def _on_built(self,payload):
        if not self._running or self._context is None or not isinstance(payload,dict): return
        self._seen+=1
        action=str(payload.get("action") or OPEN).upper()
        if action != OPEN:
            self._legal+=1
            await self._context.publish(EVENT_LEGAL,dict(payload))
            return
        neutral=self._neutral(payload)
        perpetual=self._perpetual(payload)
        account=str(payload.get("account_id") or "")
        symbol=str(payload.get("symbol") or "")
        side=str(payload.get("side") or "").upper()
        ref=num(payload.get("reference_price"))
        sl=num(payload.get("stop_loss"))
        tp=num(payload.get("take_profit"))
        volume=num(payload.get("volume"))
        spec=self._specs.get(account+"\x1f"+symbol)
        reason=""
        if not account or not symbol or side not in (BUY,SELL): reason="BAD_ACCOUNT_SYMBOL_OR_SIDE"
        elif spec is None: reason="NO_SYMBOL_SPECS"
        elif ref is None or ref<=0 or volume is None or volume<=0: reason="INCOMPLETE_ORDER"
        if not reason:
            rounded=self._round_volume(volume,spec)
            if rounded<=0: reason="VOLUME_BELOW_MINIMUM"
            elif num(spec.get("volume_max")) is not None and rounded>num(spec.get("volume_max")): reason="VOLUME_ABOVE_MAXIMUM"
            else: volume=rounded
        if reason:
            self._rejected+=1
            await self._context.publish(EVENT_REJECTED,{**payload,"reason":reason,"stage":"STOP_LEGALITY"})
            return
        if neutral:
            out=dict(payload)
            out["volume"]=volume
            self._legal+=1
            await self._context.publish(EVENT_LEGAL,out)
            return
        if sl is None or sl<=0: reason="INCOMPLETE_ORDER"
        elif not perpetual and (tp is None or tp<=0): reason="INCOMPLETE_ORDER"
        elif perpetual and side==BUY and not sl<ref: reason="BUY_LEVELS"
        elif perpetual and side==SELL and not sl>ref: reason="SELL_LEVELS"
        elif not perpetual and side==BUY and not sl<ref<tp: reason="BUY_LEVELS"
        elif not perpetual and side==SELL and not tp<ref<sl: reason="SELL_LEVELS"
        if reason:
            self._rejected+=1
            await self._context.publish(EVENT_REJECTED,{**payload,"reason":reason,"stage":"STOP_LEGALITY"})
            return
        point=num(spec.get("point")) or num(spec.get("tick_size")) or 0.0
        stops=num(spec.get("stops_level")) or 0.0
        freeze=num(spec.get("freeze_level")) or 0.0
        minimum_distance=max(stops,freeze,self._hard_floor_points)*point+self._buffer*point
        requested_distance=abs(ref-sl)
        out=dict(payload)
        out["volume"]=volume
        if point>0 and requested_distance<minimum_distance:
            factor=requested_distance/minimum_distance if minimum_distance>0 else 1.0
            volume=self._round_volume(volume*factor,spec)
            sl=ref-minimum_distance if side==BUY else ref+minimum_distance
            tp=None if perpetual else (ref+self._reward*minimum_distance if side==BUY else ref-self._reward*minimum_distance)
            if volume<=0:
                self._rejected+=1
                await self._context.publish(EVENT_REJECTED,{**payload,"reason":"STOP_LEGALITY_VOLUME_TOO_SMALL","stage":"STOP_LEGALITY"})
                return
            out.update({"volume":volume,"stop_loss":round(sl,8),"take_profit":None if perpetual else round(tp,8),"legality_adjusted":True})
        out.update({"min_stop_distance":minimum_distance,"freeze_level":spec.get("freeze_level"),"stop_buffer":self._buffer})
        self._legal+=1
        await self._context.publish(EVENT_LEGAL,out)

    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        d={"seen":self._seen,"legal":self._legal,"rejected":self._rejected,"specs":len(self._specs)}
        if not self._seen:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_ORDER_STOP_CHECK | legal=0 rejected=0",details=d)
        return HealthStatus(state=HealthState.HEALTHY,message="legal=%d rejected=%d"%(self._legal,self._rejected),details=d)
