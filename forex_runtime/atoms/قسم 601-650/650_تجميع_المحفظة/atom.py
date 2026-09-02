from __future__ import annotations
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION="2.1.0"
COMPONENT_EVENTS=("portfolio.account.tracked","portfolio.account.state","portfolio.balance.state","portfolio.equity.state","portfolio.margin.state","portfolio.free_margin.state","portfolio.margin_level.state","portfolio.pnl.state","portfolio.open_positions.state")
EVENT_LEDGER="risk.asset_ledger.state"
EVENT_OUT="portfolio.components.state"
EVENT_SUMMARY="portfolio.summary"
EVENT_PULSE="SYS_SECOND"

class Atom(AtomBase):
    def __init__(self):
        self._context=None
        self._running=False
        self._components={}
        self._ledger={}
        self._positions={}
        self._last=None
        self._updates=0
        # Contract 90-11 (2026-08-19): "pulse" = SYS_SECOND (project-wide
        # definition, atom constitution 03 §13, applied without exception
        # everywhere else in the codebase). Cache the latest state from each
        # source; publish only on the pulse, and only if something changed.
        self._dirty=False; self._last_pulse=None
    async def initialize(self,context):
        self._context=context
        for event in COMPONENT_EVENTS: context.subscribe(event,self._on_component)
        context.subscribe(EVENT_LEDGER,self._on_ledger)
        context.subscribe(EVENT_PULSE,self._on_pulse)
    async def start(self): self._running=True
    async def stop(self): self._running=False
    async def shutdown(self): await self.stop()
    async def _on_component(self,payload):
        if not self._running or not isinstance(payload,dict): return
        account=str(payload.get("account_id") or "")
        if not account:return
        name=str(payload.get("component") or "")
        if not name:
            event=str(payload.get("event_name") or ""); name=event.removeprefix("portfolio.").removesuffix(".state")
        self._components.setdefault(account,{})[name]=dict(payload); self._dirty=True
    async def _on_ledger(self,payload):
        if not self._running or not isinstance(payload,dict):return
        for row in payload.get("ledgers",[]) if isinstance(payload.get("ledgers"),list) else []:
            if isinstance(row,dict) and row.get("symbol"):
                self._ledger[(str(row.get("account_id") or ""),str(row.get("symbol")))]=dict(row); self._dirty=True
    async def _on_pulse(self,payload):
        if not self._running or not isinstance(payload,dict): return
        self._last_pulse=payload
        if not self._dirty: return
        await self._publish()
    async def _publish(self):
        if self._context is None:return
        accounts=[]
        for account,components in sorted(self._components.items()):
            row={"account_id":account,"components":components,"read_only":True}
            row.update(components.get("account",{}))
            accounts.append(row)
        assets={}
        for (account,symbol),row in self._ledger.items():
            assets.setdefault(symbol,{"symbol":symbol,"accounts":[]})["accounts"].append({"account_id":account,"R":row.get("R"),"K":row.get("K"),"X":row.get("X"),"u":row.get("u"),"loss_exposure":row.get("loss_exposure"),"net":row.get("v_net"),"gross":sum(float(x.get("volume") or 0) for x in row.get("positions",[]))})
        pulse=self._last_pulse or {}
        out={"accounts":accounts,"assets":list(assets.values()),"components":self._components,"read_only":True,
             "count_accounts":len(accounts),"count_assets":len(assets),
             "sequence":pulse.get("sequence"),"pulse_id":pulse.get("pulse_id")}
        self._last=out;self._updates+=1;self._dirty=False
        await self._context.publish(EVENT_OUT,out); await self._context.publish(EVENT_SUMMARY,out)
    async def health_check(self):
        if not self._running:return HealthStatus(state=HealthState.UNHEALTHY,message="NOT_STARTED")
        return HealthStatus(state=HealthState.HEALTHY if self._last else HealthState.DEGRADED,message="components=%d"%sum(len(v) for v in self._components.values()),details={"accounts":len(self._components),"updates":self._updates})
