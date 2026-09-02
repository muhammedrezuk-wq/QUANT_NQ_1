from __future__ import annotations

import math
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.cycle_identity import cycle_key
from shared.financial_truth import EVENT_SHORTAGE, FinancialTruth, bind_truth
from shared.financial_scope import account_broker, financial_key, row_key, text

ATOM_VERSION = "4.3.0"
FINANCIAL_SCOPE_PARTS = 3
EVENT_IN = "market.tick.validated"
EVENT_ACCOUNT = "platform.account.state"
EVENT_SPECS = "market.symbol_specs"
EVENT_SPECS_CTRADER = "market.ctrader.symbol_specs"
EVENT_STOP = "risk.structure_stop.state"
EVENT_OUT = "risk.position_size.state"
EVENT_REJECTED = "risk.position_size.rejected"
METHOD = "risk_percent_sizing"
MAX_RISK_PER_TRADE_PCT = 5.0
_BUDGET_TOLERANCE = 1.01
_PERCENT = 100.0
_DP = 6
VOLUME_EPSILON = 1e-12


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._risk_pct = 1.0
        self._stop_pct = 0.5
        self._min_lot = 0.01
        self._max_lot = 1.0
        self._lot_step = 0.01
        self._broker_by_account: dict[str, str] = {}
        self._truth = FinancialTruth("513")
        self._specs: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._pending_specs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self._stops: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._candles_seen = 0
        self._emitted = 0
        self._rejected = 0
        self._config_error = ""
        self._specs_max_age_s = 600.0
        self._active_scopes: set[tuple[str, str, str]] = set()
        self._unavailable_scopes: dict[tuple[str, str, str], str] = {}
        self._sized_by_symbol_fallback = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._risk_pct = float(cfg["risk_per_trade_pct"])
        self._stop_pct = float(cfg["default_stop_pct"])
        self._min_lot = float(cfg["min_lot"])
        self._max_lot = float(cfg["max_lot"])
        self._lot_step = float(cfg["lot_step"])
        self._specs_max_age_s = float(cfg.get("specs_max_age_s", 600.0))
        if not (0.0 < self._risk_pct <= MAX_RISK_PER_TRADE_PCT):
            self._config_error = "RISK_PER_TRADE_PCT_OUT_OF_RANGE"
        elif self._stop_pct <= 0 or self._lot_step <= 0 or self._min_lot <= 0 \
                or self._max_lot < self._min_lot:
            self._config_error = "INVALID_POSITION_SIZE_CONFIG"
        context.subscribe(EVENT_IN, self._on_tick)
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        bind_truth(self, context, self._truth, ("equity",))
        context.subscribe(EVENT_SPECS, self._on_specs)
        context.subscribe(EVENT_SPECS_CTRADER, self._on_specs)
        context.subscribe(EVENT_STOP, self._on_stop)

    async def start(self) -> None: self._running = True
    async def stop(self) -> None: self._running = False
    async def shutdown(self) -> None: await self.stop()

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        owner = account_broker(payload, self._broker_by_account)
        if owner is None: return
        account, broker = owner
        self._broker_by_account[account] = broker
        if not self._truth.has(account, "equity") and self._context is not None:
            await self._context.publish(EVENT_SHORTAGE, self._truth.shortage_body(
                account, "equity", broker=broker, detail="513 position sizing"))
        self._retry_pending_specs()

    def _retry_pending_specs(self) -> None:
        # Unit 2 (4.2.0): pending spec rows survive a failed retry while their
        # account still has no broker known -- they wait for the next chance,
        # they are never dropped on the floor.
        if not self._pending_specs:
            return
        pending, self._pending_specs = self._pending_specs, []
        for parent, row in pending:
            if not self._store_spec(parent, row):
                account = text(row.get("account_id") or parent.get("account_id"))
                if account and account not in self._broker_by_account:
                    self._pending_specs.append((parent, row))

    def _store_spec(self, payload: dict[str, Any], row: dict[str, Any]) -> bool:
        key = row_key(payload, row, self._broker_by_account)
        tick_value = number(row.get("tick_value")); tick_size = number(row.get("tick_size"))
        if key is None or tick_value is None or tick_size is None or tick_value <= 0 or tick_size <= 0:
            return False
        published = number(row.get("spec_published_at") or payload.get("published_at"))
        source_age = max(0.0, clock.now()-published) if published is not None else 0.0
        self._specs[key] = {"tick_value": tick_value, "tick_size": tick_size,
                            "contract_size": number(row.get("contract_size")) or 0.0,
                            "volume_min": number(row.get("volume_min")) or self._min_lot,
                            "volume_max": number(row.get("volume_max")) or self._max_lot,
                            "volume_step": number(row.get("volume_step")) or self._lot_step,
                            "received_monotonic": clock.mono(), "source_age_at_receipt": source_age,
                            "spec_published_at": published}
        return True

    async def _on_specs(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        rows = payload.get("symbols")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict): continue
            if not self._store_spec(payload, row):
                account = text(row.get("account_id") or payload.get("account_id"))
                if account and account not in self._broker_by_account:
                    self._pending_specs.append((dict(payload), dict(row)))

    async def _on_stop(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        key = financial_key(payload, payload.get("symbol"), self._broker_by_account)
        if key is None: return
        self._active_scopes.add(key)
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        self._stops[key] = {"buy_stop": number(meta.get("buy_stop")),
                            "sell_stop": number(meta.get("sell_stop"))}

    def _spec(self, key: tuple[str, str, str]) -> tuple[dict[str, Any] | None, str]:
        spec, reason, _ = self._spec_resolved(key)
        return spec, reason

    def _fresh(self, spec: dict[str, Any]) -> bool:
        age = (clock.mono() - float(spec.get("received_monotonic") or 0.0) +
               float(spec.get("source_age_at_receipt") or 0.0))
        return 0.0 <= age <= self._specs_max_age_s

    def _spec_resolved(self, key: tuple[str, str, str]) -> tuple[dict[str, Any] | None, str, bool]:
        # Financial specifications are broker-scoped. A unique row under a
        # different broker is still the wrong contract and must never size an
        # order. Broker-less bridge rows are held pending until account identity
        # is known, then stored under that broker by _store_spec.
        spec = self._specs.get(key)
        if spec is not None and self._fresh(spec):
            return spec, "", False
        if spec is not None:
            return None, "STALE_ACCOUNT_SYMBOL_SPECS", False
        return None, "SIZING_UNAVAILABLE_FOR_SYMBOL", False

    def _lot(self, equity: float, distance: float, spec: dict[str, Any]) -> tuple[float | None, str]:
        risk_amount = equity * self._risk_pct / _PERCENT
        denom = distance * spec["tick_value"] / spec["tick_size"]
        if denom <= 0: return None, "INVALID_STOP_DISTANCE"
        raw = risk_amount / denom
        step = spec.get("volume_step") or self._lot_step
        stepped = round(raw / step) * step
        if stepped * denom > risk_amount * _BUDGET_TOLERANCE:
            stepped = math.floor(raw / step) * step
        broker_min = max(self._min_lot, spec.get("volume_min") or 0.0)
        broker_max = min(self._max_lot, spec.get("volume_max") or self._max_lot)
        if stepped + VOLUME_EPSILON < broker_min:
            return None, "VOLUME_BELOW_BROKER_MIN"
        return round(min(broker_max, stepped), _DP), ""

    async def _reject(self, payload: dict[str, Any], reason: str) -> None:
        self._rejected += 1
        if self._context is not None:
            await self._context.publish(EVENT_REJECTED, {
                "account_id": payload.get("account_id"), "broker": payload.get("broker"),
                "symbol": payload.get("symbol"), "reason": reason,
                "status": "REJECTED", "usable_for_order": False})

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict): return
        self._candles_seen += 1
        if self._config_error:
            await self._reject(payload, self._config_error); return
        key = financial_key(payload, payload.get("symbol"), self._broker_by_account)
        close = number(payload.get("price"))
        if key is None:
            await self._reject(payload, "MISSING_ACCOUNT_BROKER_OR_SYMBOL"); return
        account, broker, symbol = key
        # Unit 2 (4.2.0): the live tick is itself an identity source -- learn
        # the account's broker from it, then give any pending spec rows a
        # second chance instead of waiting for the next account state.
        if self._broker_by_account.get(account) != broker:
            self._broker_by_account[account] = broker
            self._retry_pending_specs()
        self._active_scopes.add(key)
        if close is None or close <= 0:
            await self._reject(payload, "INVALID_TICK_PRICE"); return
        equity = self._truth.get(account, "equity")
        if equity is None or equity <= 0:
            await self._reject(payload, "NO_ACCOUNT_EQUITY"); return
        spec, spec_reason, spec_fallback = self._spec_resolved(key)
        if spec is None:
            self._unavailable_scopes[key] = spec_reason
            await self._reject(payload, spec_reason); return
        if spec_fallback:
            self._sized_by_symbol_fallback += 1
        self._unavailable_scopes.pop(key, None)
        risk_amount = equity * self._risk_pct / _PERCENT
        default_distance = close * self._stop_pct / _PERCENT
        lot, default_reason = self._lot(equity, default_distance, spec)
        buy_lot = sell_lot = buy_stop = sell_stop = None
        reasons = [default_reason] if default_reason else []
        stops = self._stops.get(key)
        if stops:
            candidate = stops.get("buy_stop")
            if candidate is not None and candidate < close:
                buy_stop = candidate; buy_lot, reason = self._lot(equity, close - candidate, spec)
                if reason: reasons.append("BUY_" + reason)
            candidate = stops.get("sell_stop")
            if candidate is not None and candidate > close:
                sell_stop = candidate; sell_lot, reason = self._lot(equity, candidate - close, spec)
                if reason: reasons.append("SELL_" + reason)
        status = "OK" if any(x is not None for x in (lot, buy_lot, sell_lot)) else "REJECTED"
        if status == "REJECTED":
            await self._reject(payload, reasons[0] if reasons else "NO_EXECUTABLE_VOLUME")
        timeframe = str(payload.get("timeframe") or "")
        period = payload.get("period_start", payload.get("timestamp", ""))
        await self._context.publish(EVENT_OUT, {
            "account_id": account, "broker": broker, "symbol": symbol,
            "id": "position_sizing", "cycle_id": cycle_key(
                account_id=account, broker=broker, symbol=symbol,
                timeframe=timeframe, period_start=period),
            "status": status, "approved": status == "OK", "reason": reasons[0] if reasons else "",
            "timeframe": timeframe, "warnings": list(dict.fromkeys(reasons)),
            "metadata": {"method": METHOD, "lot": lot,
                         "risk_amount": round(risk_amount, 2), "risk_pct": self._risk_pct,
                         "stop_distance": round(default_distance, _DP), "equity": round(equity, 2),
                         "price": close, "buy_lot": buy_lot, "buy_stop": buy_stop,
                         "sell_lot": sell_lot, "sell_stop": sell_stop}})
        self._emitted += 1

    async def snapshot(self) -> dict[str, Any]:
        now = clock.mono()
        return {"version": ATOM_VERSION, "brokers": dict(self._broker_by_account),
                "financial_truth": self._truth.export(),
                "specs": [{"scope": list(key), "value": {**value,
                           "remaining_s": max(0.0, self._specs_max_age_s-(now-float(value.get("received_monotonic") or now)+float(value.get("source_age_at_receipt") or 0.0)))}}
                          for key, value in self._specs.items()],
                "active_scopes": [list(key) for key in self._active_scopes]}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_POSITION_SIZE_STATE")
        self._broker_by_account = {str(key): str(value) for key, value in
                                   (state.get("brokers") or {}).items()}
        self._truth.load(state.get("financial_truth"))
        self._specs = {}
        for item in state.get("specs", []):
            if not isinstance(item, dict) or not isinstance(item.get("scope"), list) or len(item["scope"]) != FINANCIAL_SCOPE_PARTS:
                continue
            value = dict(item.get("value") or {}); remaining = max(0.0, float(value.pop("remaining_s", 0.0)))
            source_age=float(value.get("source_age_at_receipt") or 0.0)
            elapsed=max(0.0,self._specs_max_age_s-remaining-source_age)
            value["received_monotonic"] = clock.mono()-elapsed
            self._specs[tuple(item["scope"])] = value
        self._active_scopes = {tuple(item) for item in state.get("active_scopes", [])
                               if isinstance(item, list) and len(item) == FINANCIAL_SCOPE_PARTS}

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        unavailable = {"|".join(key): self._spec(key)[1] for key in self._active_scopes
                       if self._spec(key)[0] is None}
        details = {"accounts": self._truth.accounts, "config_error": self._config_error,
                   "specs": len(self._specs), "active_scopes": [list(key) for key in sorted(self._active_scopes)],
                     "unavailable_scopes": unavailable, "specs_max_age_s": self._specs_max_age_s,
                     "symbol_fallback_sized": self._sized_by_symbol_fallback,
                     "pending_specs": len(self._pending_specs),
                     "emitted": self._emitted, "rejected": self._rejected}
        if self._config_error: return HealthStatus(state=HealthState.UNHEALTHY, message=self._config_error, details=details)
        if not self._truth.accounts: return HealthStatus(state=HealthState.DEGRADED, message="NO_EQUITY_YET", details=details)
        if not self._active_scopes:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_FINANCIAL_CANDLE | sized=0 specs=%d" % len(self._specs),
                                details=details)
        if unavailable:
            reason = "STALE_ACCOUNT_SYMBOL_SPECS" if "STALE_ACCOUNT_SYMBOL_SPECS" in unavailable.values() else "SIZING_UNAVAILABLE_FOR_SYMBOL"
            return HealthStatus(state=HealthState.DEGRADED, message=reason, details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="sized=%d rejected=%d" % (self._emitted, self._rejected),
                            details=details)
