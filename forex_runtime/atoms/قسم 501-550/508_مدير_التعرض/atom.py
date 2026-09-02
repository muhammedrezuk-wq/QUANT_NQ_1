from __future__ import annotations

import math
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_truth import EVENT_SHORTAGE, FinancialTruth, bind_truth
from shared.financial_scope import account_broker, financial_key, row_key, text

ATOM_VERSION = "3.1.0"
# v3.1.0 (2026-08-25, owner ruling): GROSS is the exposure counter, NET is
# directional information only. A breached gross limit refuses NEW exposure
# (usable_for_new_exposure goes False, which 552 applies to OPEN orders
# only) -- closes, exits and management never pass through that gate, so
# "GROSS HIGH" can no longer blindly block everything. A hedged pair on a
# small account is NOT zero exposure: two legs carry margin, spread and
# counterparty-leg risk, so net never substitutes for gross.
FINANCIAL_SCOPE_PARTS = 3
EVENT_POSITIONS = "platform.positions.state"
EVENT_ACCOUNT = "platform.account.state"
EVENT_SPECS = "market.symbol_specs"
EVENT_ORDER = "execution.order.built"
EVENT_REJECTED = "execution.order.rejected"
EVENT_TRADE = "platform.trade_event"
EVENT_OUT = "risk.exposure.state"
EVENT_HALT_REQUEST = "risk.halt.requested"


def number(value: Any) -> float | None:
    if isinstance(value, bool): return None
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._dropped = 0
        self._context: AtomContext | None = None
        self._running = False
        self._max_exposure_pct = 0.0
        self._broker_by_account: dict[str, str] = {}
        self._specs: dict[tuple[str, str, str], dict[str, float]] = {}
        self._pending_specs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self._truth = FinancialTruth("508")
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._breached: set[tuple[str, str]] = set()
        self._last: dict[tuple[str, str], dict[str, Any]] = {}
        self._missing_specs = 0
        self._emitted = 0
        self._specs_max_age_s = 600.0
        self._active_scopes: set[tuple[str, str, str]] = set()
        self._unknown_positions: dict[str, dict[str, Any]] = {}
        self._picture_usable: dict[tuple[str, str], bool] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._max_exposure_pct = float(context.config["max_exposure_pct"])
        self._specs_max_age_s = float(context.config.get("specs_max_age_s", 600.0))
        context.subscribe(EVENT_SPECS, self._on_specs)
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        bind_truth(self, context, self._truth, ("equity",))
        context.subscribe(EVENT_POSITIONS, self._on_positions)
        context.subscribe(EVENT_ORDER, self._on_order)
        context.subscribe(EVENT_REJECTED, self._release)
        context.subscribe(EVENT_TRADE, self._release)

    async def start(self) -> None: self._running = True
    async def stop(self) -> None: self._running = False
    async def shutdown(self) -> None: await self.stop()

    def _store_spec(self, key: tuple[str, str, str], size: float,
                    published_at: Any = None) -> None:
        published=number(published_at);source_age=max(0.0,clock.now()-published) if published is not None else 0.0
        self._specs[key] = {"contract_size": size, "received_monotonic": clock.mono(),
                            "source_age_at_receipt":source_age,"spec_published_at":published}

    def _spec_size(self, key: tuple[str, str, str]) -> tuple[float | None, str]:
        spec = self._specs.get(key)
        if spec is None:
            return None, "MISSING_ACCOUNT_SYMBOL_SPECS"
        age = (clock.mono()-float(spec.get("received_monotonic") or 0.0)+
               float(spec.get("source_age_at_receipt") or 0.0))
        if age < 0 or age > self._specs_max_age_s:
            return None, "STALE_ACCOUNT_SYMBOL_SPECS"
        return float(spec["contract_size"]), ""

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account = text(payload.get("account_id")); broker = text(payload.get("broker"))
        if account and broker:
            self._broker_by_account[account] = broker
            if not self._truth.has(account, "equity") and self._context is not None:
                await self._context.publish(EVENT_SHORTAGE, self._truth.shortage_body(
                    account, "equity", broker=broker, detail="508 exposure"))
            pending, self._pending_specs = self._pending_specs, []
            for parent, row in pending:
                key = row_key(parent, row, self._broker_by_account); size = number(row.get("contract_size"))
                if key is not None and size is not None and size > 0: self._store_spec(key,size,row.get("spec_published_at") or parent.get("published_at"))
                else: self._pending_specs.append((parent, row))

    async def _on_specs(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        rows = payload.get("symbols")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict): continue
            key = row_key(payload, row, self._broker_by_account)
            size = number(row.get("contract_size"))
            if key is not None and size is not None and size > 0:
                self._store_spec(key,size,row.get("spec_published_at") or payload.get("published_at"))
            elif text(row.get("account_id") or payload.get("account_id")):
                self._pending_specs.append((dict(payload), dict(row)))

    async def _on_order(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        if str(payload.get("action") or "OPEN").upper() != "OPEN": return
        key = financial_key(payload, payload.get("symbol"), self._broker_by_account)
        request_id = text(payload.get("request_id")); volume = number(payload.get("volume"))
        price = number(payload.get("reference_price"))
        if key is None or not request_id or volume is None or price is None: return
        self._active_scopes.add(key)
        size, _ = self._spec_size(key)
        if size is None:
            self._missing_specs += 1; return
        self._pending[(key[0], request_id)] = {
            "scope": key, "notional": abs(volume) * price * size}

    async def _release(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account = text(payload.get("account_id")); request = text(payload.get("request_id"))
        if account and request: self._pending.pop((account, request), None)

    async def _on_positions(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        rows = payload.get("positions")
        if not isinstance(rows, list): return
        source = text(payload.get("source")) or "positions"
        self._unknown_positions = {key: value for key, value in self._unknown_positions.items()
                                   if not key.startswith(source + "|")}
        candidates = list(rows)
        extra = payload.get("unknown_positions")
        if isinstance(extra, list): candidates.extend(row for row in extra if isinstance(row, dict))
        totals: dict[tuple[str, str], float] = {}; counts: dict[tuple[str, str], int] = {}
        nets: dict[tuple[str, str], float] = {}; net_known: dict[tuple[str, str], bool] = {}
        missing: dict[tuple[str, str], int] = {}; diagnostics: dict[tuple[str, str], list[dict[str, Any]]] = {}
        hint = account_broker(payload, self._broker_by_account)
        for index, row in enumerate(candidates):
            if not isinstance(row, dict): continue
            merged = dict(payload); merged.update(row)
            key = financial_key(merged, row.get("symbol"), self._broker_by_account)
            volume = number(row.get("volume")); price = number(row.get("current_price"))
            if price is None: price = number(row.get("entry_price"))
            reasons=[]; size=None
            if key is None: reasons.append("MISSING_POSITION_SCOPE")
            else:
                self._active_scopes.add(key); size, spec_reason = self._spec_size(key)
                if size is None: reasons.append(spec_reason)
            if volume is None: reasons.append("MISSING_POSITION_VOLUME")
            if price is None: reasons.append("MISSING_POSITION_PRICE")
            if row.get("account_id_status") not in (None,"AVAILABLE"): reasons.append("ACCOUNT_ID_UNAVAILABLE")
            owner = key[:2] if key is not None else hint
            if reasons:
                identity = source + "|" + text(row.get("ticket") or index)
                unknown = {**row,"status":"UNKNOWN_POSITION","unknown_reasons":sorted(set(reasons)),
                           "source":source,"identity":identity}
                self._unknown_positions[identity]=unknown
                if owner is not None:
                    missing[owner]=missing.get(owner,0)+1;diagnostics.setdefault(owner,[]).append(unknown)
                continue
            assert key is not None and size is not None and owner is not None
            leg_notional=abs(volume)*price*size
            totals[owner]=totals.get(owner,0.0)+leg_notional
            counts[owner]=counts.get(owner,0)+1
            side=text(row.get("side")).upper()
            sign=1.0 if side in ("BUY","LONG") else -1.0 if side in ("SELL","SHORT") else None
            if sign is None: net_known[owner]=False
            else:
                nets[owner]=nets.get(owner,0.0)+sign*leg_notional
                net_known.setdefault(owner,True)
        global_unknown=[row for row in self._unknown_positions.values()
                        if account_broker(row,self._broker_by_account) is None]
        owners=set(totals)|{(a,self._broker_by_account.get(a,"")) for a in self._truth.account_ids()}|set(missing)
        if hint is not None: owners.add(hint)
        for owner in owners:
            if global_unknown:
                missing[owner]=missing.get(owner,0)+len(global_unknown)
                diagnostics.setdefault(owner,[]).extend(global_unknown)
            self._picture_usable[owner] = payload.get("usable_for_new_exposure") is True
            net=nets.get(owner,0.0) if net_known.get(owner,counts.get(owner,0)==0) else None
            await self._evaluate(owner,totals.get(owner,0.0),counts.get(owner,0),
                                 missing.get(owner,0),payload.get("timestamp"),
                                 diagnostics.get(owner,[]),self._picture_usable[owner],net)

    async def _evaluate(self, owner: tuple[str, str], notional: float, count: int,
                        missing: int, stamp: Any, diagnostics: list[dict[str, Any]],
                        picture_usable: bool, net: float | None = None) -> None:
        if self._context is None: return
        account, broker = owner; equity = self._truth.get(account, "equity")
        known = equity is not None and missing == 0 and picture_usable
        pct = round(notional / equity * 100.0, 4) if known and equity else None
        over = bool(known and self._max_exposure_pct > 0 and pct is not None and pct >= self._max_exposure_pct)
        pending_count = sum(1 for value in self._pending.values() if value["scope"][:2] == owner)
        # Owner ruling 2026-08-25: hedge_ratio = 1 fully hedged, 0 naked.
        hedge = round(1.0 - abs(net) / notional, 4) if net is not None and notional > 0 else None
        body = {"account_id": account, "broker": broker, "id": "exposure_manager",
                "status": "OK" if known else "UNKNOWN_POSITION", "notional": round(notional, 4),
                "gross_exposure": round(notional, 4),
                "net_exposure": round(net, 4) if net is not None else None,
                "hedge_ratio": hedge,
                "equity": equity, "exposure_pct": pct, "max_exposure_pct": self._max_exposure_pct,
                "open_positions": count, "pending_orders": pending_count,
                "missing_specs": missing, "unknown_position_count": missing,
                "unknown_positions": diagnostics, "breached": over,
                "usable_for_risk": known,
                # Breach refuses NEW exposure only: 552 applies this to OPEN
                # orders; closes and management never pass through that gate.
                "usable_for_new_exposure": known and not over,
                "usable_for_protection": True}
        value = number(stamp)
        if value is not None: body["timestamp"] = value
        self._last[owner] = body; self._emitted += 1
        await self._context.publish(EVENT_OUT, body)
        if over and owner not in self._breached:
            self._breached.add(owner)
            await self._context.publish(EVENT_HALT_REQUEST, {
                "account_id": account, "broker": broker, "reason": "MAX_EXPOSURE",
                "origin": "508", "exposure_pct": pct, "limit": self._max_exposure_pct})
        elif known and not over: self._breached.discard(owner)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION,
                "brokers": dict(self._broker_by_account),
                "financial_truth": self._truth.export(),
                "specs": [{"account_id": a, "broker": b, "symbol": s, "value": v} for (a,b,s),v in self._specs.items()],
                "pending": [{"account_id": a, "request_id": r, **v} for (a,r),v in self._pending.items()],
                "unknown_positions": list(self._unknown_positions.values()),
                "active_scopes": [list(key) for key in self._active_scopes]}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict): raise ValueError("INVALID_EXPOSURE_STATE")
        self._broker_by_account = {str(k): str(v) for k,v in (state.get("brokers") or {}).items()}
        self._truth.load(state.get("financial_truth"))
        self._specs = {}
        for item in state.get("specs", []):
            if not isinstance(item, dict): continue
            key=(text(item.get("account_id")),text(item.get("broker")),text(item.get("symbol")))
            value=item.get("value")
            if all(key) and isinstance(value,dict) and number(value.get("contract_size")) is not None:
                self._specs[key]=dict(value)
        self._unknown_positions={str(item.get("identity")):dict(item) for item in state.get("unknown_positions",[])
                                 if isinstance(item,dict) and item.get("identity")}
        self._active_scopes={tuple(item) for item in state.get("active_scopes",[])
                            if isinstance(item,list) and len(item)==FINANCIAL_SCOPE_PARTS}
        self._pending={}
        for item in state.get("pending",[]):
            if not isinstance(item,dict):continue
            account=text(item.get("account_id"));request=text(item.get("request_id"));scope=item.get("scope")
            if account and request and isinstance(scope,(list,tuple)) and len(scope)==FINANCIAL_SCOPE_PARTS:
                self._pending[(account,request)]={"scope":tuple(scope),"notional":float(item.get("notional") or 0.0)}

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        unavailable={"|".join(key):self._spec_size(key)[1] for key in self._active_scopes
                     if self._spec_size(key)[0] is None}
        details = {"accounts": self._truth.accounts, "specs": len(self._specs),
                   "active_scopes":[list(key) for key in sorted(self._active_scopes)],
                   "unavailable_scopes":unavailable,"unknown_positions":len(self._unknown_positions),
                   "pending": len(self._pending), "missing_specs": self._missing_specs,
                   "breached": [list(x) for x in sorted(self._breached)], "emitted": self._emitted}
        if not self._truth.accounts: return HealthStatus(state=HealthState.DEGRADED, message="ACCOUNT_STATE_UNKNOWN", details=details)
        if self._unknown_positions: return HealthStatus(state=HealthState.DEGRADED, message="UNKNOWN_POSITION", details=details)
        if not self._active_scopes:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_ORDER_OR_POSITION | pending=0 emitted=%d" % self._emitted,
                                details=details)
        if unavailable: return HealthStatus(state=HealthState.DEGRADED, message="ACTIVE_SYMBOL_SPECS_UNAVAILABLE", details=details)
        if self._breached:
            samples=[]
            for owner in sorted(self._breached):
                last=self._last.get(owner) or {}
                samples.append("%s gross=%s net=%s hedge=%s pct=%s/%s" % (
                    owner[0], last.get("gross_exposure"), last.get("net_exposure"),
                    last.get("hedge_ratio"), last.get("exposure_pct"), self._max_exposure_pct))
            return HealthStatus(state=HealthState.DEGRADED,
                                message="EXPOSURE_BREACHED_NEW_ONLY_BLOCKED: " + " | ".join(samples),
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY, message="exposure_known", details=details)
