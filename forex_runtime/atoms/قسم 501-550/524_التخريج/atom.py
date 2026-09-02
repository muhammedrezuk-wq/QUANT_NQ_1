from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "3.1.0"
# v3.1.0 (2026-08-27, item 16/27 of the 27-atom review -- "silent restore
# on data corruption"): restore() silently RETURNED on a non-dict state
# (leaving self at its empty __init__ defaults, no error, no log) and
# then mutated self._issued/_confirmed/_pending/... through a sequence of
# field parses that could each raise -- a failure partway through left a
# torn mix of old and new milestone-issuance bookkeeping. That bookkeeping
# is what stops a milestone from being extracted twice; silently losing
# or tearing it on a corrupt snapshot risks re-issuing (real money)
# extraction requests for milestones already issued or even confirmed in
# the prior run. Fixed the same shape as 520: non-dict state now raises,
# and every field is built into a local first, committed to self only
# once ALL of them parse successfully.

EVENT_LEDGER = "risk.asset_ledger.state"
EVENT_STATE = "asset.extraction.state"
EVENT_PARTIAL = "asset.extraction.requested"
EVENT_FULL = "asset.full_extraction.requested"
EVENT_CONFIRM = "asset.extraction.confirmed"
EVENT_FAILED = "asset.extraction.failed"
EVENT_EXTRACTED = "risk.asset_profit.extracted"

KIND_PARTIAL = "partial"
KIND_FULL = "full"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_DATA = "NO_LEDGER_YET"

SEP = "|"
MONEY_DP = 4
LOOP_CAP = 60


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _key(account: Any, symbol: Any) -> str:
    return str(account or "") + SEP + str(symbol or "")


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context = None
        self._running = False
        self._mult = 2.0
        self._fraction = 0.5
        self._full_targets: dict[str, float] = {}
        self._default_full = 0.0
        self._issued: dict[str, set[int]] = {}
        self._confirmed: dict[str, set[int]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._full_issued: set[str] = set()
        self._last_ledgers: dict[str, dict[str, Any]] = {}
        self._last_state: dict[str, Any] | None = None
        self._partials = 0
        self._fulls = 0
        self._confirmations = 0
        self._updates = 0
        self._failures = 0
        self._failure_ids: set[str] = set()

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._mult = float(cfg.get("milestone_mult", 2.0))
        self._fraction = float(cfg.get("extract_fraction", 0.5))
        self._default_full = _number(cfg.get("default_full_target")) or 0.0
        raw = cfg.get("full_targets") if isinstance(cfg.get("full_targets"), dict) else {}
        for k, value in raw.items():
            amount = _number(value)
            if amount is not None and amount > 0:
                self._full_targets[str(k)] = amount
        context.subscribe(EVENT_LEDGER, self._on_ledger)
        context.subscribe(EVENT_CONFIRM, self._on_confirm)
        context.subscribe(EVENT_FAILED, self._on_failed)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _full_target(self, key: str) -> float:
        return self._full_targets.get(key, self._default_full)

    def _highest(self, gross: float, budget: float) -> int:
        if gross < budget or budget <= 0 or self._mult <= 1:
            return -1
        level = budget
        highest = -1
        for _ in range(LOOP_CAP):
            if gross < level:
                break
            highest += 1
            level *= self._mult
        return highest

    def _request_id(self, account: str, symbol: str, k: int) -> str:
        return "extract-%s-%s-m%d" % (account, symbol, k)

    async def _on_ledger(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        rows = payload.get("ledgers")
        if not isinstance(rows, list):
            return
        levels: list[dict[str, Any]] = []
        for led in rows:
            if not isinstance(led, dict):
                continue
            symbol = str(led.get("symbol") or led.get("asset_canonical") or "")
            if not symbol:
                continue
            account = str(led.get("account_id") or "")
            k = _key(account, symbol)
            self._last_ledgers[k] = dict(led)
            budget = _number(led.get("R", led.get("risk_budget", led.get("budget"))))
            gross = _number(led.get("realized_gross", led.get("gross_profit")))
            if gross is None:
                gross = _number(led.get("realized_net", led.get("realized_pnl")))
                if gross is None and "floating_economic" not in led and "floating" not in led:
                    gross = _number(led.get("net"))
            if not led.get("budgeted") or budget is None or budget <= 0 or gross is None:
                levels.append(self._level(led, -1, budget, gross, False))
                continue
            highest = self._highest(max(0.0, gross), budget)
            for milestone_k in range(highest + 1):
                await self._issue_partial_if_needed(account, symbol, budget, gross, milestone_k)
            await self._issue_full_if_needed(account, symbol, budget, gross)
            levels.append(self._level(led, highest, budget, gross, True))

        out = {"levels": levels, "requests": [dict(v) for v in self._pending.values()],
               "count": len(levels), "request_count": len(self._pending),
               "confirmed_count": self._confirmations}
        if payload.get("timestamp") is not None:
            out["timestamp"] = payload.get("timestamp")
        self._last_state = out
        self._updates += 1
        await self._context.publish(EVENT_STATE, out)

    def _level(self, led: dict[str, Any], highest: int, budget: float | None,
               gross: float | None, eligible: bool) -> dict[str, Any]:
        account = str(led.get("account_id") or "")
        symbol = str(led.get("symbol") or led.get("asset_canonical") or "")
        k = _key(account, symbol)
        issued = sorted(self._issued.get(k, set()))
        confirmed = sorted(self._confirmed.get(k, set()))
        next_value = (budget * (self._mult ** (highest + 1))
                      if budget is not None and budget > 0 else None)
        return {
            "account_id": account, "symbol": symbol,
            "R": budget, "budget": budget,
            "gross_profit": None if gross is None else round(gross, MONEY_DP),
            "K": led.get("K"), "X": led.get("X"),
            "highest_milestone": highest, "next_milestone": next_value,
            "issued_milestones": issued, "confirmed_milestones": confirmed,
            "pending": [dict(v) for v in self._pending.values()
                        if _key(v.get("account_id"), v.get("symbol")) == k],
            "full_target": self._full_target(k),
            "full_issued": k in self._full_issued,
            "eligible": eligible,
        }

    async def _issue_partial_if_needed(self, account: str, symbol: str, budget: float,
                                       gross: float, milestone_k: int) -> None:
        k = _key(account, symbol)
        issued = self._issued.setdefault(k, set())
        if milestone_k in issued:
            return
        request_id = self._request_id(account, symbol, milestone_k)
        if request_id in self._pending:
            return
        milestone = budget * (self._mult ** milestone_k)
        amount = self._fraction * milestone
        request = {
            "extraction_id": request_id, "request_id": request_id,
            "account_id": account, "symbol": symbol, "kind": KIND_PARTIAL,
            "amount": round(amount, MONEY_DP), "milestone_k": milestone_k,
            "milestone": round(milestone, MONEY_DP),
            "gross_profit": round(gross, MONEY_DP), "status": "REQUESTED",
        }
        issued.add(milestone_k)
        self._pending[request_id] = dict(request)
        self._partials += 1
        await self._context.publish(EVENT_PARTIAL, request)

    async def _issue_full_if_needed(self, account: str, symbol: str, budget: float,
                                    gross: float) -> None:
        k = _key(account, symbol)
        target = self._full_target(k)
        if target <= 0 or gross < target or k in self._full_issued:
            return
        request_id = "extract-full-%s-%s" % (account, symbol)
        if request_id in self._pending:
            return
        request = {"extraction_id": request_id, "request_id": request_id,
                   "account_id": account, "symbol": symbol, "kind": KIND_FULL,
                   "target": target, "amount": target,
                   "gross_profit": round(gross, MONEY_DP), "status": "REQUESTED"}
        self._full_issued.add(k)
        self._pending[request_id] = dict(request)
        self._fulls += 1
        await self._context.publish(EVENT_FULL, request)

    async def _on_failed(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        request_id = str(payload.get("extraction_id") or payload.get("request_id") or "")
        if not request_id:
            return
        failure_id = str(payload.get("failure_id") or "%s:%s:%s" % (
            request_id, payload.get("ticket"), payload.get("reason")))
        if failure_id in self._failure_ids:
            return
        self._failure_ids.add(failure_id); self._failures += 1
        request = self._pending.get(request_id)
        if request is None:
            request = {"extraction_id": request_id, "request_id": request_id,
                       "account_id": payload.get("account_id"),
                       "symbol": payload.get("symbol"), "kind": payload.get("kind")}
            self._pending[request_id] = request
        request.update({"status": "FAILED", "failure_reason": payload.get("reason"),
                        "attempts": payload.get("attempts"),
                        "last_attempt_at": payload.get("last_attempt_at"),
                        "ticket": payload.get("ticket")})
        await self._context.publish(EVENT_STATE, {
            "failure": dict(request), "status": "FAILED",
            "reason": payload.get("reason"),
            "requests": [dict(value) for value in self._pending.values()],
            "failed_count": self._failures, "confirmed_count": self._confirmations})

    async def _on_confirm(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        request_id = str(payload.get("extraction_id") or payload.get("request_id") or "")
        request = self._pending.get(request_id)
        if request is None or request.get("status") == "CONFIRMED":
            return
        actual = _number(payload.get("actual_amount", payload.get("amount")))
        if actual is None or actual <= 0:
            return
        amount = min(actual, _number(request.get("amount")) or actual)
        request["status"] = "CONFIRMED"
        request["confirmed_amount"] = round(amount, MONEY_DP)
        request["confirmed_at"] = payload.get("timestamp")
        self._pending.pop(request_id, None)
        self._confirmed.setdefault(_key(request.get("account_id"), request.get("symbol")), set()).add(
            int(request.get("milestone_k", -1))) if request.get("kind") == KIND_PARTIAL else None
        self._confirmations += 1
        await self._context.publish(EVENT_EXTRACTED, {
            "extraction_id": request_id, "request_id": request_id,
            "account_id": request.get("account_id"), "symbol": request.get("symbol"),
            "amount": round(amount, MONEY_DP), "milestone_k": request.get("milestone_k"),
            "kind": request.get("kind"), "confirmed": True,
            "actual_ticket": payload.get("ticket"),
        })
        await self._context.publish(EVENT_STATE, {
            "confirmation": dict(request), "confirmed_count": self._confirmations,
            "requests": [dict(v) for v in self._pending.values()],
        })

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        failed = sum(1 for value in self._pending.values() if value.get("status") == "FAILED")
        details = {"updates": self._updates, "partials": self._partials,
                   "fulls": self._fulls, "confirmations": self._confirmations,
                   "failures": self._failures, "failed_pending": failed,
                   "pending": len(self._pending)}
        if self._last_state is None:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_DATA, details=details)
        if failed:
            return HealthStatus(state=HealthState.DEGRADED,
                                message="EXTRACTION_FAILED_PENDING", details=details)
        if self._pending:
            return HealthStatus(state=HealthState.HEALTHY,
                                message="waiting_for_actual_confirmation=%d" % len(self._pending),
                                details=details)
        return HealthStatus(state=HealthState.HEALTHY,
                            message="confirmed=%d" % self._confirmations, details=details)

    async def snapshot(self) -> dict[str, Any]:
        return {
            "version": ATOM_VERSION,
            "issued": {k: sorted(v) for k, v in self._issued.items()},
            "confirmed": {k: sorted(v) for k, v in self._confirmed.items()},
            "pending": list(self._pending.values()),
            "full_issued": sorted(self._full_issued),
            "failure_ids": sorted(self._failure_ids), "failures": self._failures,
        }

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_EXTRACTION_STATE")
        new_issued = {str(k): {int(v) for v in values if isinstance(v, (int, float))}
                      for k, values in (state.get("issued") or {}).items()
                      if isinstance(values, list)}
        new_confirmed = {str(k): {int(v) for v in values if isinstance(v, (int, float))}
                         for k, values in (state.get("confirmed") or {}).items()
                         if isinstance(values, list)}
        new_pending = {str(v.get("extraction_id")): dict(v)
                       for v in state.get("pending", [])
                       if isinstance(v, dict) and v.get("extraction_id")}
        new_full_issued = {str(k) for k in state.get("full_issued", [])}
        new_failure_ids = {str(value) for value in state.get("failure_ids", [])}
        new_failures = int(state.get("failures") or 0)
        self._issued = new_issued
        self._confirmed = new_confirmed
        self._pending = new_pending
        self._full_issued = new_full_issued
        self._failure_ids = new_failure_ids
        self._failures = new_failures
