from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.financial_scope import financial_key, row_key, text

ATOM_VERSION = "2.0.3"
# v2.0.3 (2026-08-27, item 23/27 of the 27-atom review -- restore()
# crashes if the stored value is not a dict): the top-level state WAS
# guarded (isinstance check, raises ValueError), but the three nested
# fields (brokers/requests/stats) were not -- state.get("brokers") (etc.)
# feeding straight into `.items()` crashes with a raw AttributeError if
# that field is present but corrupted into a non-dict, non-empty value
# (a list, a string, a number). A failure partway through also left self
# torn: self._brokers could already be committed while self._requests
# crashed. Fixed by guarding each nested field and building all three
# into locals before committing any of them to self.
EVENT_REQUEST = "trading.final_decision"
EVENT_TRADE = "platform.trade_event"
EVENT_REJECTED = "execution.order.rejected"
EVENT_FAILED = "platform.brain_signal.write_failed"
EVENT_SPECS = "market.symbol_specs"
EVENT_ACCOUNT = "platform.account.state"
EVENT_OUT = "execution.quality.state"
BUY = "BUY"


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def scope_text(key: tuple[str, str, str]) -> str:
    return "\x1f".join(key)


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context = None; self._running = False
        self._brokers: dict[str, str] = {}
        self._specs: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._pending_specs: list[dict[str, Any]] = []
        self._requests: dict[str, dict[str, Any]] = {}
        self._stats: dict[str, dict[str, Any]] = {}
        self._max_adverse = 50.0; self._max_reject_rate = 0.25; self._min_samples = 1
        self._seen = 0; self._emitted = 0; self._unmeasurable = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context; cfg = context.config
        self._max_adverse = float(cfg.get("max_adverse_points", 50.0))
        self._max_reject_rate = float(cfg.get("max_reject_rate", 0.25))
        self._min_samples = max(1, int(cfg.get("min_samples", 1)))
        for event, handler in ((EVENT_REQUEST, self._on_request), (EVENT_TRADE, self._on_trade),
                               (EVENT_REJECTED, self._on_rejected), (EVENT_FAILED, self._on_failed),
                               (EVENT_SPECS, self._on_specs), (EVENT_ACCOUNT, self._on_account)):
            context.subscribe(event, handler)

    async def start(self) -> None: self._running = True
    async def stop(self) -> None: self._running = False
    async def shutdown(self) -> None: await self.stop()

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account = text(payload.get("account_id")); broker = text(payload.get("broker"))
        if account and broker:
            self._brokers[account] = broker
            pending, self._pending_specs = self._pending_specs, []
            for item in pending: await self._on_specs(item)

    async def _on_specs(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        for row in payload.get("symbols", []) if isinstance(payload.get("symbols"), list) else []:
            if not isinstance(row, dict): continue
            key = row_key(payload, row, self._brokers)
            point = number(row.get("point")) or number(row.get("tick_size"))
            if key is not None and point is not None and point > 0:
                spec = dict(row); spec["measurement_point"] = point
                spec["spec_digest"] = hashlib.sha256(json.dumps(
                    spec, sort_keys=True, default=str).encode()).hexdigest()
                self._specs[key] = spec
            elif text(row.get("account_id") or payload.get("account_id")) \
                    and payload not in self._pending_specs:
                self._pending_specs.append(dict(payload))

    def _bucket(self, key: str) -> dict[str, Any]:
        return self._stats.setdefault(key, {
            "executions": 0, "rejections": 0, "bridge_failures": 0,
            "unmeasurable": 0, "adverse_sum": 0.0, "adverse_max": 0.0, "last": None})

    async def _on_request(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        request_id = text(payload.get("request_id"))
        key = financial_key(payload, payload.get("symbol"), self._brokers)
        if not request_id or key is None: return
        spec = self._specs.get(key)
        self._requests[request_id] = {
            "account_id": key[0], "broker": key[1], "symbol": key[2],
            "side": text(payload.get("side")).upper(),
            "reference_price": number(payload.get("reference_price")),
            "point": spec.get("measurement_point") if spec else None,
            "spec_digest": spec.get("spec_digest") if spec else None,
        }

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        request_id = text(payload.get("request_id")); request = self._requests.get(request_id)
        if text(payload.get("event_type")).upper() != "OPENED" or request is None: return
        key = scope_text((request["account_id"], request["broker"], request["symbol"]))
        bucket = self._bucket(key); fill = number(payload.get("entry_price"))
        reference = request.get("reference_price"); point = request.get("point")
        if fill is None or reference is None or point is None or point <= 0:
            self._unmeasurable += 1; bucket["unmeasurable"] += 1
            bucket["last"] = {"request_id": request_id, "measured": False,
                              "reason": "SLIPPAGE_UNMEASURABLE"}
            await self._publish(key); return
        signed = (fill - reference) * (1.0 if request.get("side") == BUY else -1.0)
        adverse = max(0.0, signed / point)
        bucket["executions"] += 1; bucket["adverse_sum"] += adverse
        bucket["adverse_max"] = max(bucket["adverse_max"], adverse)
        bucket["last"] = {"request_id": request_id, "measured": True,
                          "adverse_points": adverse, "spec_digest": request.get("spec_digest")}
        await self._publish(key)

    def _event_scope(self, payload: dict[str, Any]) -> str | None:
        key = financial_key(payload, payload.get("symbol"), self._brokers)
        return scope_text(key) if key is not None else None

    async def _on_rejected(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        key = self._event_scope(payload)
        if key is not None: self._bucket(key)["rejections"] += 1; await self._publish(key)

    async def _on_failed(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        key = self._event_scope(payload)
        if key is not None: self._bucket(key)["bridge_failures"] += 1; await self._publish(key)

    async def _publish(self, key: str) -> None:
        if self._context is None: return
        bucket = self._bucket(key)
        total = bucket["executions"] + bucket["rejections"] + bucket["bridge_failures"]
        rejects = bucket["rejections"] + bucket["bridge_failures"]
        rate = rejects / total if total else 0.0
        if bucket["unmeasurable"]:
            status = "BLOCKED"
        elif bucket["adverse_max"] > self._max_adverse \
                or (total >= self._min_samples and rate > self._max_reject_rate):
            status = "BLOCKED"
        else:
            status = "HEALTHY" if bucket["executions"] else "WAITING"
        account, broker, symbol = key.split("\x1f", 2)
        out = {"account_id": account, "broker": broker, "symbol": symbol,
               "status": status, "executions": bucket["executions"],
               "rejections": bucket["rejections"], "bridge_failures": bucket["bridge_failures"],
               "unmeasurable": bucket["unmeasurable"], "reject_rate": rate,
               "adverse_max_points": bucket["adverse_max"],
               "adverse_mean_points": (bucket["adverse_sum"] / bucket["executions"]
                                       if bucket["executions"] else None),
               "last": bucket["last"],
               "limits": {"max_adverse_points": self._max_adverse,
                          "max_reject_rate": self._max_reject_rate}}
        self._seen += 1; self._emitted += 1
        await self._context.publish(EVENT_OUT, out)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "brokers": dict(self._brokers),
                "requests": dict(self._requests), "stats": dict(self._stats)}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict): raise ValueError("INVALID_EXECUTION_QUALITY_STATE")
        raw_brokers = state.get("brokers")
        raw_requests = state.get("requests")
        raw_stats = state.get("stats")
        new_brokers = ({str(k): str(v) for k, v in raw_brokers.items()}
                       if isinstance(raw_brokers, dict) else {})
        new_requests = ({str(k): dict(v) for k, v in raw_requests.items() if isinstance(v, dict)}
                        if isinstance(raw_requests, dict) else {})
        new_stats = ({str(k): dict(v) for k, v in raw_stats.items() if isinstance(v, dict)}
                     if isinstance(raw_stats, dict) else {})
        self._brokers = new_brokers
        self._requests = new_requests
        self._stats = new_stats

    async def health_check(self) -> HealthStatus:
        if not self._running: return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {"seen": self._seen, "emitted": self._emitted,
                   "scopes": len(self._stats), "unmeasurable": self._unmeasurable}
        blocked = any(v.get("unmeasurable") or v.get("adverse_max", 0) > self._max_adverse
                      for v in self._stats.values())
        if blocked: return HealthStatus(state=HealthState.DEGRADED, message="QUALITY_BLOCKED", details=details)
        if not self._stats:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="READY_AWAITING_FIRST_MEASURED_TRADE | quality_scopes=0",
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="quality_scopes=%d" % len(self._stats), details=details)
