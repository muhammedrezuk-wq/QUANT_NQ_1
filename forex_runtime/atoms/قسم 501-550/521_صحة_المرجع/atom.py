from __future__ import annotations

import math
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION="2.4.0"
# v2.3.0 (2026-08-25): the execution venue's own tick (MT5, 618) is a
# PRIMARY reference source beside cTrader. Measured root: BTCUSD's only
# reference was the cTrader stream, whose transport saturates in bursts
# (tick age 26-45s vs the 5s window) -- six built orders died on
# REFERENCE_NOT_USABLE inside one stale window while the broker the orders
# were going TO was streaming fresh prices the whole time. Freshness
# windows and every validity check stay exactly as they are; the jump
# guard (500) absorbs the few-dollar spread between the two venues.
EVENT_PRIMARY="feed.ctrader.tick"
EVENT_PRIMARY_MT5="feed.mt5.tick"
EVENT_FALLBACK="market.reference"
EVENT_PULSE="SYS_SECOND"
EVENT_OUT="reference.health.state"
PRIMARY="primary"
FALLBACK="fallback"
DEFAULT_MAX_AGE=5.0
DEFAULT_FALLBACK_AGE=30.0
DEFAULT_MAX_SPREAD=10.0
DEFAULT_MAX_JUMP=100.0
DEFAULT_DWELL=0.0


def num(v: Any)->float|None:
    try:r=float(v)
    except (TypeError,ValueError):return None
    return r if math.isfinite(r) else None

def text(v: Any, default="")->str:
    if v is None:return default
    r=str(v).strip()
    return r or default

def source_stamp(p):
    for k in ("exchange_timestamp","source_timestamp","timestamp","received_at"):
        v=num(p.get(k))
        if v is not None:return v
    return None

class Atom(AtomBase):
    def __init__(self):
        self._context=None
        self._running=False
        self._feeds={}
        self._last_prices={}
        # ٢٠٢٦-٠٩-٠١: يبقى مخزَّنًا للمراقبة فقط — الحكم لا يعتمد عليه.
        self._now=None
        self._states={}
        self._max_age=5.0
        self._fallback_age=30.0
        self._max_spread=10.0
        self._max_jump=100.0
        self._dwell=0.0
        self._symbols=set()
        self._seen=0
        self._rejected=0
        self._published=0
    async def initialize(self,context):
        self._context=context
        cfg=context.config
        self._symbols={text(x) for x in cfg.get("symbols",[]) if text(x)}
        self._max_age=num(cfg.get("max_data_age_s")) or DEFAULT_MAX_AGE
        self._fallback_age=num(cfg.get("fallback_max_age_s")) or DEFAULT_FALLBACK_AGE
        self._max_spread=num(cfg.get("max_sane_spread")) or DEFAULT_MAX_SPREAD
        self._max_jump=num(cfg.get("max_tick_jump")) or DEFAULT_MAX_JUMP
        self._dwell=num(cfg.get("min_dwell_s")) or DEFAULT_DWELL
        context.subscribe(EVENT_PRIMARY,self._on_primary)
        context.subscribe(EVENT_PRIMARY_MT5,self._on_primary)
        context.subscribe(EVENT_FALLBACK,self._on_fallback)
        context.subscribe(EVENT_PULSE,self._on_pulse)
    async def start(self): self._running=True
    async def stop(self): self._running=False
    async def shutdown(self): await self.stop()
    def feed(self,s): return self._feeds.setdefault(s,{PRIMARY:{},FALLBACK:{}})
    def allowed(self,s): return not self._symbols or s in self._symbols
    def fresh(self,f,age):
        """طزاجة العيّنة مقيسةً على السلطة الزمنيّة، لا على حمولة النبضة.

        ٢٠٢٦-٠٩-٠١ (مقيس حيًّا): كان «الآن» يُؤخذ من `official_time` داخل
        نبضة `SYS_SECOND` الواصلة عبر صندوق بريد. القياس على النواة الحيّة:
        النبضة تصل كاملةً (‏`delivered=1,032,593` · `dropped=0`) لكن طابعها
        يتأخّر تأخّرًا تراكميًّا، فيصير أحدث من طابعها **طابعُ التِكّة نفسه**؛
        فيخرج `self._now - ts` **سالبًا** ويسقط شرط `0.0 <= …`، فتُصنَّف كل
        عيّنة «غير طازجة» ويُعلَن `NO_USABLE_REFERENCE` بينما 622 سليمة
        وتُسلّم 147,363 تِكّة و613 تمرّر تسعة رموز. لا مرجع مفقود — بل ساعةٌ
        وصلت متأخّرة. القراءة الآن من `clock` مباشرة بلا طابور.
        **حدود الطزاجة لم تُمَسّ حرفًا** (`max_age`/`fallback_age` كما هي)."""
        if not f.get("valid") or f.get("timestamp") is None:return False
        return 0.0<=clock.now()-f["timestamp"]<=age
    def jump_ok(self,source,s,p):
        key=source+"|"+s
        old=self._last_prices.get(key)
        self._last_prices[key]=p
        return not(old is not None and self._max_jump>0 and abs(p-old)>self._max_jump)
    async def _on_primary(self,p):
        if not self._running or not isinstance(p,dict):return
        s=text(p.get("symbol"));
        if not s or not self.allowed(s):return
        bid=num(p.get("bid"))
        ask=num(p.get("ask"))
        price=num(p.get("price"))
        if price is None and bid is not None and ask is not None:price=(bid+ask)/2
        ts=source_stamp(p)
        f=self.feed(s)[PRIMARY]
        reason=""
        if price is None or price<=0:reason="BAD_PRICE"
        elif bid is None or ask is None or bid<=0 or ask<=0:reason="BAD_BID_ASK"
        elif ask<bid:reason="CROSSED_MARKET"
        elif ask-bid>self._max_spread:reason="WIDE_SPREAD"
        elif not self.jump_ok(PRIMARY,s,price):reason="TICK_JUMP"
        elif ts is None:reason="MISSING_SOURCE_TIMESTAMP"
        elif f.get("timestamp") is not None and ts<=f["timestamp"]:return
        self._seen+=1
        f.update({"valid":not reason,"reason":reason,"timestamp":ts,"price":price,"bid":bid,"ask":ask,"spread":ask-bid if bid is not None and ask is not None else None})
        if reason:self._rejected+=1
        await self._publish(s)
    async def _on_fallback(self,p):
        if not self._running or not isinstance(p,dict):return
        s=text(p.get("symbol"));
        if not s or not self.allowed(s):return
        price=num(p.get("value")) or num(p.get("price"))
        ts=source_stamp(p)
        f=self.feed(s)[FALLBACK]
        reason=""
        if price is None or price<=0:reason="BAD_PRICE"
        elif not self.jump_ok(FALLBACK,s,price):reason="TICK_JUMP"
        elif ts is None:reason="MISSING_SOURCE_TIMESTAMP"
        elif f.get("timestamp") is not None and ts<=f["timestamp"]:return
        self._seen+=1
        f.update({"valid":not reason,"reason":reason,"timestamp":ts,"price":price,"provider":text(p.get("provider"),"fallback")})
        if reason:self._rejected+=1
        await self._publish(s)
    async def _on_pulse(self,p):
        if not self._running or not isinstance(p,dict):return
        now=num(p.get("official_time"))
        if now is None:return
        self._now=now
        for s in list(self._feeds):await self._publish(s)
    def candidate(self,s):
        f=self.feed(s)
        p=self.fresh(f[PRIMARY],self._max_age)
        q=self.fresh(f[FALLBACK],self._fallback_age)
        candidate=PRIMARY if p else FALLBACK if q else None
        current=f.get("selected",{}).get("provider")
        if candidate and current and candidate!=current and f["selected"].get("at") is not None and clock.now()-f["selected"]["at"]<self._dwell:
            candidate=current
        if candidate!=current:f["selected"]={"provider":candidate,"at":clock.now()}
        return candidate,p,q
    async def _publish(self,s):
        if self._context is None:return
        provider,p,q=self.candidate(s)
        f=self.feed(s)
        selected=f.get(provider,{}) if provider else {}
        state="HEALTHY" if provider==PRIMARY else "FALLBACK" if provider==FALLBACK else "STALE" if f[PRIMARY].get("timestamp") or f[FALLBACK].get("timestamp") else "INVALID"
        ts=selected.get("timestamp")
        age=clock.now()-ts if ts is not None else None
        out={"symbol":s,"state":state,"selected_provider":provider,"selected_price":selected.get("price"),"source_timestamp":ts,"data_age_s":age,"primary":dict(f[PRIMARY]),"fallback":dict(f[FALLBACK]),"warnings":[]}
        self._states[s]=out
        await self._context.publish(EVENT_OUT,out)
        self._published+=1
    def state(self,s):return self._states.get(s)
    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        good=sum(x.get("state") in ("HEALTHY","FALLBACK") for x in self._states.values())
        d={"symbols":len(self._feeds),"seen":self._seen,"rejected":self._rejected,"published":self._published,"usable":good}
        if not self._states:return HealthStatus(state=HealthState.DEGRADED,message="NO_REFERENCE_DATA",details=d)
        return HealthStatus(state=HealthState.HEALTHY if good else HealthState.DEGRADED,message="reference_available" if good else "NO_USABLE_REFERENCE",details=d)
