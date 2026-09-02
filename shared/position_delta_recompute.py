from __future__ import annotations

from typing import Any

SEP = "\x1f"
GATE_MARK = "_gate"
FILTER_PASSED = "FILTER_PASSED"
FILTER_BLOCKED = "FILTER_BLOCKED"
PRICE_SOURCE = "mt5_broker_feed"
BUY = "buy"
SELL = "sell"
WAIT = "wait"
ADD = "ADD"
REDUCE = "REDUCE"
HEDGE = "HEDGE"
REBALANCE = "REBALANCE"
HOLD = "HOLD"
BLOCKED = "BLOCKED"
EVENT_OUT = "perpetual.target.state"
REASON_NEUTRAL_KEEP = "NEUTRAL_KEEP_GROSS"


def real(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def key(account: Any, symbol: Any) -> str:
    return str(account or "") + SEP + str(symbol or "")


def cycle_rank(cycle: Any) -> float | None:
    try:
        return float(str(cycle or "").rsplit("|", 1)[-1])
    except (TypeError, ValueError):
        return None


def is_stale(incoming: Any, accepted: float | None) -> bool:
    rank = cycle_rank(incoming)
    return rank is not None and accepted is not None and rank < accepted


def finish_targets(
    atom: Any, out: dict[str, Any], target_net: float, gross: float,
    target_buy: float, target_sell: float, current_buy: float,
    current_sell: float, reason: str,
) -> None:
    raw_buy = target_buy - current_buy
    raw_sell = target_sell - current_sell
    delta_buy = max(-atom._max_step, min(atom._max_step, raw_buy))
    delta_sell = max(-atom._max_step, min(atom._max_step, raw_sell))
    active = (
        abs(delta_buy) >= atom._min_volume
        or abs(delta_sell) >= atom._min_volume
    )
    if not active:
        delta_buy = delta_sell = 0.0
        action = HOLD
    elif delta_buy < -atom._min_volume or delta_sell < -atom._min_volume:
        action = (
            REBALANCE
            if delta_buy > atom._min_volume or delta_sell > atom._min_volume
            else REDUCE
        )
    else:
        action = HEDGE if target_net * (out.get("current_net") or 0.0) < 0 else ADD
    out.update({
        "status": "READY",
        "target_net": round(target_net, 8),
        "target_gross": round(gross, 8),
        "target_buy": round(target_buy, 8),
        "target_sell": round(target_sell, 8),
        "delta_buy": round(delta_buy, 8),
        "delta_sell": round(delta_sell, 8),
        "delta_net": round(delta_buy - delta_sell, 8),
        "action": action,
        "reason": reason,
    })


async def recompute(atom: Any, scope_key: str) -> None:
    if atom._context is None:
        return
    account, symbol = scope_key.split(SEP, 1)
    decision = atom._decisions.get(scope_key)
    wildcard = atom._decisions.get(key("*", symbol))
    ledger = atom._ledgers.get(scope_key)
    if decision is None:
        decision = wildcard
    elif (
        wildcard is not None
        and wildcard.get(GATE_MARK)
        and not decision.get(GATE_MARK)
        and not is_stale(
            wildcard.get("cycle_id"), cycle_rank(decision.get("cycle_id"))
        )
    ):
        decision = wildcard
    if decision is None or ledger is None:
        return

    legs = list(atom._positions.get(scope_key, []))
    current_buy = sum(row["volume"] for row in legs if row["side"] == "BUY")
    current_sell = sum(row["volume"] for row in legs if row["side"] == "SELL")
    current_net = current_buy - current_sell
    current_gross = current_buy + current_sell
    direction = str(decision.get("direction") or decision.get("signal") or WAIT).lower()
    direction = (
        BUY if direction in ("buy", "up", "long")
        else SELL if direction in ("sell", "down", "short")
        else WAIT
    )
    strength = real(decision.get("strength"))
    fallback_strength = (real(decision.get("score")) or 0.0) / 100.0
    strength = max(0.0, min(1.0, strength if strength is not None else fallback_strength))
    budget = real(ledger.get("risk_budget", ledger.get("R", ledger.get("budget"))))
    filter_verdict = atom._filter_verdict(scope_key, decision)
    if filter_verdict != FILTER_PASSED:
        direction = WAIT
        if filter_verdict == FILTER_BLOCKED:
            atom._blocked += 1

    price = atom._price.get(scope_key)
    dial = atom._dials.get(scope_key, {})
    hard_stop = atom._stops.get(scope_key, {})
    stop_frac = real(dial.get("stop_distance_frac"))
    value_per_unit = atom._vpu.get(scope_key)
    portfolio = atom._portfolios.get(scope_key)
    state = str((portfolio or {}).get("state") or "UNKNOWN").upper()
    out = {
        "account_id": account, "symbol": symbol, "direction": direction,
        "strength": strength, "decision_id": decision.get("decision_id"),
        "gate_request_id": decision.get("gate_request_id"),
        "current_buy": round(current_buy, 8),
        "current_sell": round(current_sell, 8),
        "current_net": round(current_net, 8),
        "current_gross": round(current_gross, 8),
        "target_net": None, "target_gross": None,
        "target_buy": None, "target_sell": None,
        "delta_net": 0.0, "delta_buy": 0.0, "delta_sell": 0.0,
        "action": HOLD, "status": "WAITING", "reason": "",
        "current_legs": legs,
        "reference_price": round(price, 8) if price else None,
        "price_source": PRICE_SOURCE,
        "reference_is_broker_feed": True,
        "stop_distance_frac": stop_frac,
        "stop_state": (
            "FROZEN" if state in ("FROZEN", "PAUSED")
            else "REBALANCING" if state in ("WARNING", "HEDGING")
            else "READY"
        ),
        "state": state, "filter_verdict": filter_verdict,
        "version": atom._version(scope_key),
    }
    account_mode = str((portfolio or {}).get("account_mode") or "UNKNOWN").upper()
    system_alive = (portfolio or {}).get("system_alive") is True

    if portfolio is None:
        out.update(status="BLOCKED", action=BLOCKED, reason="PORTFOLIO_STATE_MISSING")
    elif not system_alive:
        out.update(status="BLOCKED", action=BLOCKED, reason="SYSTEM_NOT_ALIVE")
    elif account_mode != "HEDGING":
        reason = "NETTING_UNSUPPORTED" if account_mode == "NETTING" else "ACCOUNT_MODE_UNKNOWN"
        out.update(status="BLOCKED", action=BLOCKED, reason=reason)
    elif str(hard_stop.get("status") or "").upper() == "FROZEN":
        out.update(status="BLOCKED", action=BLOCKED, reason="HARD_STOP_FROZEN")
    elif state in ("FROZEN", "PAUSED"):
        out.update(status="BLOCKED", action=BLOCKED, reason="PORTFOLIO_FROZEN")
    elif state in ("WARNING", "HEDGING"):
        target_net = 0.0
        gross_cap = atom._gross_cap(scope_key, budget, price, stop_frac, value_per_unit)
        gross = min(current_gross, gross_cap)
        target_buy = target_sell = gross / 2.0
        finish_targets(
            atom, out, target_net, gross, target_buy, target_sell,
            current_buy, current_sell, "RISK_REBALANCE",
        )
        out["risk_gross_cap"] = round(gross_cap, 8)
    elif (
        budget is None or budget <= 0 or price is None or price <= 0
        or stop_frac is None or stop_frac <= 0
        or value_per_unit is None or value_per_unit <= 0
    ):
        if direction == WAIT:
            target_net = 0.0
            gross = current_gross
            target_buy = target_sell = gross / 2.0
            finish_targets(
                atom, out, target_net, gross, target_buy, target_sell,
                current_buy, current_sell, "NO_DIRECTION",
            )
        else:
            out.update(
                status="WAITING", action=BLOCKED,
                reason="MISSING_R_PRICE_DIAL_OR_SPECS",
            )
    else:
        capacity = min(atom._max_target, budget / (price * stop_frac * value_per_unit))
        gross_cap = atom._gross_cap(scope_key, budget, price, stop_frac, value_per_unit)
        held, reason = atom._held_direction(scope_key, direction, strength, current_net)
        exposure = atom._fraction(strength)
        hedge = atom._hedge_fraction(strength)
        if filter_verdict != FILTER_PASSED:
            exposure = 0.0
            hedge = 1.0
        previous_strength = atom._last_strength.get(scope_key)
        previous_gross = atom._last_gross_target.get(scope_key)
        # عقد المحورين v1.1 §3 — تحذير المالك اللفظي (نصّه الحرفي، مختوم NQ):
        #   RISK_DIAL
        #   = بوابة لنمو التعرض الجديد
        #   ≠ بوابة للبقاء
        #   ≠ عامل في E(S)
        #   ≠ عامل في gross_cap
        #   ≠ عامل في R_B
        dial_pct = real(getattr(atom, "_risk_dial", lambda: 100.0)())
        dial_factor = max(0.0, min(1.0, (dial_pct if dial_pct is not None else 0.0) / 100.0))
        u_float = real(ledger.get("u_float")) or 0.0
        u_realized = real(ledger.get("u_realized")) or 0.0
        consumed_budget = max(u_float, u_realized) * budget
        remaining_rb = budget - consumed_budget
        dial_add_budget = budget * dial_factor - consumed_budget
        remaining_add_budget = max(0.0, dial_add_budget)
        if exposure <= 0.0:
            gross = min(current_gross, gross_cap)
            base_target = gross
            allowed_increase = 0.0
            decrease = 0.0
            reason = REASON_NEUTRAL_KEEP
        else:
            base_target = min(capacity * exposure, gross_cap)
            if (
                previous_strength is not None and strength < previous_strength
                and previous_gross is not None
            ):
                base_target = min(base_target, previous_gross)
            increase = max(0.0, base_target - current_gross)
            allowed_increase = min(increase, capacity * exposure * dial_factor)
            if dial_add_budget <= 0.0:
                allowed_increase = 0.0
            decrease = max(0.0, current_gross - base_target)
            gross = current_gross + allowed_increase - decrease
        target_net = (
            0.0 if held is None
            else gross * (1.0 - hedge) * (1.0 if held == BUY else -1.0)
        )
        target_buy = max(0.0, (gross + target_net) / 2.0)
        target_sell = max(0.0, (gross - target_net) / 2.0)
        atom._last_strength[scope_key] = strength
        atom._last_gross_target[scope_key] = gross
        finish_targets(
            atom, out, target_net, gross, target_buy, target_sell,
            current_buy, current_sell, reason,
        )
        unit_cost = atom._spread_cost.get(scope_key, 0.0) + atom._hedge_cost_per_volume
        out.update({
            "risk_dial": round(dial_factor * 100.0, 2),
            "base_target": round(base_target, 8),
            "allowed_increase": round(allowed_increase, 8),
            "decrease": round(decrease, 8),
            "consumed_budget": round(consumed_budget, 2),
            "remaining_RB": round(remaining_rb, 2),
            "dial_add_budget": round(dial_add_budget, 2),
            "remaining_add_budget": round(remaining_add_budget, 2),
            "max_target": round(capacity, 8),
            "risk_gross_cap": round(gross_cap, 8),
            "exposure_fraction": round(exposure, 8),
            "hedge_fraction": round(hedge, 8),
            "held_direction": held or WAIT,
            "hedge_cost_per_volume": round(unit_cost, 8),
            "projected_hedge_cost": round(gross * unit_cost, 8),
            "risk_budget": budget,
            "stop_distance_frac": stop_frac,
            "reference_price": round(price, 8),
            "vpu": value_per_unit,
        })

    atom._last[scope_key] = out
    atom._seen += 1
    atom._emitted += 1
    await atom._context.publish(EVENT_OUT, out)
