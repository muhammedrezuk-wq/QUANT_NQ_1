from __future__ import annotations
from typing import Any
from pair_support import contract_name, leg_entry, new_pair, pair_status

EVENT_REQUEST = "execution.order.requested"
EVENT_PAIR_STATE = "perpetual.pair.state"
EVENT_ESCALATION = "perpetual.owner.escalation"
EVENT_ASSET_COMMAND = "risk.asset.command"
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
PROTECTION_NEUTRAL_HEDGE = "NEUTRAL_HEDGE"
STATUS_REQUESTED = "REQUESTED"
STATUS_RETRY = "RETRY_REQUESTED"
STATUS_ACTUAL = "ACTUAL"
STATUS_FAILED = "FAILED"
STATUS_EXHAUSTED = "EXHAUSTED"

def _key(account: Any, symbol: Any) -> str:
    return str(account or "") + "|" + str(symbol or "")

class PairEventMixin:
    async def _on_requested(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        pair_id = str(payload.get("pair_id") or "").strip()
        role = str(payload.get("leg_role") or "").upper()
        if role not in (SIDE_BUY, SIDE_SELL): role = ""
        request_id = str(payload.get("request_id") or "").strip()
        if not pair_id or not role or not request_id:
            return
        pair = self._pairs.setdefault(pair_id, new_pair(pair_id, payload, self._max_attempts))
        pair["legs"].setdefault(role, {}).update(leg_entry(payload, pair, role, request_id))
        self._request_map[request_id] = (pair_id, role)
        await self._publish_pair(pair)

    async def _on_validated(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        if payload.get("approved") is not False:
            return
        await self._failure_for(payload.get("request_id"), payload.get("reason", "RISK_REJECTED"), payload)

    async def _on_rejected(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        await self._failure_for(payload.get("request_id"), payload.get("reason", "ORDER_REJECTED"), payload)

    async def _on_send_failure(self, payload: dict[str, Any]) -> None:
        """Bridge write failures and EA command failures share one fate:
        the flood guard backs off for that (account, symbol)."""
        if not self._running or not isinstance(payload, dict):
            return
        self._flood_guard.mark_failure(str(payload.get("account_id") or ""), str(payload.get("symbol") or ""), self._official_time)
        await self._failure_for(payload.get("request_id"), payload.get("reason", "SEND_FAILED"), payload)

    async def _on_ack(self, payload: dict[str, Any]) -> None:
        # DONE in the bridge means the command row was processed. Only an
        # OPENED broker event with a real ticket can make a leg ACTUAL.
        if not self._running or not isinstance(payload, dict):
            return
        return

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        event_type = str(payload.get("event_type") or "").upper()
        request_id = payload.get("request_id")
        if event_type == "OPENED":
            self._flood_guard.mark_success(str(payload.get("account_id") or ""), str(payload.get("symbol") or ""))
            await self._actual_for(request_id, payload)
        elif event_type == "REJECTED":
            await self._failure_for(request_id, payload.get("reason", "BROKER_REJECTED"), payload)

    async def _actual_for(self, request_id: Any, payload: dict[str, Any]) -> None:
        request_id = str(request_id or "").strip()
        mapping = self._request_map.get(request_id)
        ticket = payload.get("ticket")
        if mapping is None or ticket in (None, "", 0):
            return
        pair_id, role = mapping
        pair = self._pairs.get(pair_id)
        if pair is None:
            return
        if (str(payload.get("account_id") or "") != str(pair.get("account_id") or "")
                or str(payload.get("symbol") or "") != str(pair.get("symbol") or "")):
            return
        leg = pair["legs"].get(role)
        if not leg or leg.get("status") == STATUS_ACTUAL:
            return
        leg.update({"status": STATUS_ACTUAL, "ticket": payload.get("ticket"),
                    "entry_price": payload.get("entry_price"),
                    "actual_volume": payload.get("volume")})
        self._actual += 1
        await self._publish_pair(pair)

    async def _failure_for(self, request_id: Any, reason: Any, payload: Any = None) -> None:
        request_id = str(request_id or "").strip()
        mapping = self._request_map.get(request_id)
        if mapping is None:
            # Owner's ruling 2026-08-14 (problem 58, option a): a delta carries
            # no pair identity, so nothing here ever reacted to its failure --
            # not even the backoff, because a rejection never calls
            # mark_failure. It is COUNTED, never acted on: the send and retry
            # decisions stay exactly as they were.
            if isinstance(payload, dict):
                self._delta_failures.record(_key(payload.get("account_id"), payload.get("symbol")), str(reason or "FAILED"))
            return
        pair_id, role = mapping
        pair = self._pairs.get(pair_id)
        if pair is None:
            return
        leg = pair["legs"].get(role)
        if not leg or leg.get("status") in (STATUS_ACTUAL, STATUS_EXHAUSTED):
            return
        # A single request can produce both a Python rejection and a bridge
        # rejection.  Once it has been replaced, the old message is ignored.
        if leg.get("request_id") != request_id or leg.get("status") == STATUS_RETRY:
            return
        leg["status"] = STATUS_FAILED
        leg["last_reason"] = str(reason or "FAILED")
        attempt = int(leg.get("attempt") or 1)
        if attempt < self._max_attempts:
            next_attempt = attempt + 1
            new_id = "%s-%s-a%d" % (pair_id, role.lower(), next_attempt)
            request = dict(leg.get("request") or {})
            request.update({"request_id": new_id, "attempt": next_attempt, "pair_id": pair_id,
                            "leg_role": role, "pair_required": True, "protection_mode": PROTECTION_NEUTRAL_HEDGE})
            leg.update({"request_id": new_id, "attempt": next_attempt, "status": STATUS_RETRY, "request": request})
            self._request_map[new_id] = (pair_id, role)
            self._retries += 1
            await self._publish_pair(pair)
            if self._context is not None:
                await self._context.publish(EVENT_REQUEST, request)
            return
        leg["status"] = STATUS_EXHAUSTED
        pair["status"] = STATUS_EXHAUSTED
        self._exhausted += 1
        await self._publish_pair(pair)
        if self._context is not None:
            await self._context.publish(EVENT_ESCALATION, {"pair_id": pair_id, "account_id": pair["account_id"],
                "symbol": pair["symbol"], "leg_role": role, "reason": leg.get("last_reason"), "attempts": attempt})
            await self._context.publish(EVENT_ASSET_COMMAND, {"account_id": pair["account_id"],
                "symbol": pair["symbol"], "command": "pause", "reason": "PAIR_RETRY_EXHAUSTED",
                "pair_id": pair_id, "leg_role": role})

    async def _publish_pair(self, pair: dict[str, Any]) -> None:
        if self._context is None:
            return
        legs = pair.get("legs", {})
        status = pair_status(legs, pair.get("status", STATUS_REQUESTED))
        pair["status"] = status
        # v5.3.0: كل تغيّر زوج يمرّ من هنا — فيُكتب دائمًا فور وقوعه،
        # فلا يضيع بموت غير نظيف (الجذر المقاس: pairs:{} بعد انهيار).
        await self._persist_pairs()
        await self._context.publish(EVENT_PAIR_STATE, {
            "pair_id": pair["pair_id"], "account_id": pair["account_id"],
            "symbol": pair["symbol"], "status": status,
            # Paper 25 section 8: the status is also published under its
            # contract name, so a reader of the paper needs no translation.
            "contract_status": contract_name(status),
            "legs": [dict(v, leg_role=k) for k, v in sorted(legs.items())],
            "successful_roles": sorted(k for k, v in legs.items() if v.get("status") == STATUS_ACTUAL),
            "failed_roles": sorted(k for k, v in legs.items() if v.get("status") in (STATUS_FAILED, STATUS_EXHAUSTED)),
        })

