from __future__ import annotations

from typing import Any

DEFAULT_RESEND_HOLD_S = 2.0
MIN_RESEND_HOLD_S = 0.1
MAX_FAILURE_BACKOFF_S = 600.0
_MAX_BACKOFF_DOUBLINGS = 16


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


class FloodGuard:
    """يحجب النية نفسها ما دامت صورة المركز لم تتغير.

    صورة المركز لا تحتوي سعر السوق: التذكرة والجهة والحجم فقط. السعر يتحرك
    طبيعيًا ولا يجوز أن يحوّل نفس المركز إلى نية جديدة كل tick.
    """

    def __init__(self, hold_s: float = DEFAULT_RESEND_HOLD_S) -> None:
        self.hold_s = max(MIN_RESEND_HOLD_S, float(hold_s))
        self._states: dict[str, dict[str, Any]] = {}
        self.suppressed = 0

    @staticmethod
    def broker_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
        legs = payload.get("current_legs") if isinstance(payload.get("current_legs"), list) else []
        rows = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            rows.append((
                str(leg.get("ticket") or ""),
                str(leg.get("side") or "").upper(),
                round(_number(leg.get("volume")), 8),
            ))
        if rows:
            return tuple(sorted(rows))
        return (
            "aggregate",
            round(_number(payload.get("current_buy")), 8),
            round(_number(payload.get("current_sell")), 8),
        )

    @staticmethod
    def target_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(payload.get("action") or "HOLD").upper(),
            round(_number(payload.get("target_buy")), 8),
            round(_number(payload.get("target_sell")), 8),
            round(_number(payload.get("target_net")), 8),
            round(_number(payload.get("delta_buy")), 8),
            round(_number(payload.get("delta_sell")), 8),
            round(_number(payload.get("delta_net")), 8),
        )

    def allows(self, account: str, symbol: str, payload: dict[str, Any], now: float | None) -> bool:
        identity = f"{account}|{symbol}"
        broker = self.broker_signature(payload)
        target = self.target_signature(payload)
        previous = self._states.get(identity)
        if previous is None:
            # Nothing is committed as "handled" before it is actually sent:
            # mark_sent promotes `pending`.  Committing here made a permission
            # look like a delivery, so an intent refused downstream was never
            # retried.
            self._states[identity] = {"target": None, "broker": None,
                                      "sent_at": None, "pending": (target, broker)}
            return True
        fail_count = int(previous.get("fail_count") or 0)
        if fail_count:
            # Commands for this (account, symbol) keep failing at the bridge:
            # back off exponentially so a dead terminal cannot be flooded.
            anchor = max(_number(previous.get("sent_at")), _number(previous.get("fail_at")))
            required = min(MAX_FAILURE_BACKOFF_S,
                           self.hold_s * (2.0 ** min(fail_count, _MAX_BACKOFF_DOUBLINGS)))
            if now is None or anchor <= 0.0 or float(now) - anchor < required:
                self.suppressed += 1
                return False
        broker_changed = previous["broker"] != broker
        same_intent = previous["target"] == target
        if same_intent and not broker_changed:
            self.suppressed += 1
            return False
        sent_at = previous.get("sent_at")
        # The hold exists to stop a *re-send*, so it may only apply once
        # something was actually sent.  "Has ever sent" is tracked separately
        # from "when", because mark_sent stamps None while no official clock has
        # arrived -- and reading that None as "hold" was a livelock: a first
        # intent refused downstream (diverged reference) never stamps a send,
        # the broker snapshot never changes because no position ever opens, and
        # every later intent stays suppressed forever.
        # Once a send has happened the old conservative rule stands unchanged:
        # without an official clock we do not re-send.
        if not broker_changed and previous.get("sent_once") and (
                now is None or sent_at is None or now - float(sent_at) < self.hold_s):
            self.suppressed += 1
            return False
        previous["pending"] = (target, broker)
        return True

    def mark_sent(self, account: str, symbol: str, now: float | None) -> None:
        identity = f"{account}|{symbol}"
        state = self._states.setdefault(identity, {})
        pending = state.pop("pending", None)
        if pending is not None:
            state["target"], state["broker"] = pending
        state["sent_at"] = now
        state["sent_once"] = True

    def mark_failure(self, account: str, symbol: str, now: float | None) -> None:
        state = self._states.setdefault(f"{account}|{symbol}", {})
        state["fail_count"] = int(state.get("fail_count") or 0) + 1
        state["fail_at"] = now

    def mark_success(self, account: str, symbol: str) -> None:
        state = self._states.setdefault(f"{account}|{symbol}", {})
        state["fail_count"] = 0
        state["fail_at"] = None

    def failing(self, account: str, symbol: str) -> int:
        return int(self._states.get(f"{account}|{symbol}", {}).get("fail_count") or 0)

    def snapshot(self) -> dict[str, Any]:
        """Serializable idempotency memory; pending permission is not committed."""
        out: dict[str, Any] = {}
        for identity, raw in self._states.items():
            state = {k: v for k, v in raw.items() if k != "pending"}
            if state.get("target") is not None:
                state["target"] = list(state["target"])
            if state.get("broker") is not None:
                state["broker"] = [list(x) if isinstance(x, tuple) else x for x in state["broker"]]
            out[identity] = state
        return {"states": out, "suppressed": self.suppressed}

    def restore(self, state: Any) -> None:
        if not isinstance(state, dict) or not isinstance(state.get("states"), dict):
            return
        restored: dict[str, dict[str, Any]] = {}
        for identity, raw in state["states"].items():
            if not isinstance(identity, str) or not isinstance(raw, dict):
                continue
            item = dict(raw)
            if isinstance(item.get("target"), list):
                item["target"] = tuple(item["target"])
            if isinstance(item.get("broker"), list):
                item["broker"] = tuple(tuple(x) if isinstance(x, list) else x for x in item["broker"])
            restored[identity] = item
        self._states = restored
        self.suppressed = int(state.get("suppressed") or 0)
