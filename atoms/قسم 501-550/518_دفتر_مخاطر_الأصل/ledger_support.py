from __future__ import annotations

import math
from typing import Any

DEFAULT_ACCOUNT = "__unknown__"
SEP = "\x1f"
POS_SEP = "\x1e"
BUY = 1.0
SELL = -1.0


def num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    result = str(value).strip()
    return result or default


def side(value: Any) -> float | None:
    value = text(value).upper()
    if value in {"BUY", "LONG", "1", "+1"}:
        return BUY
    if value in {"SELL", "SHORT", "-1"}:
        return SELL
    return None


def first(data: dict[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        value = num(data.get(name))
        if value is not None:
            return value
    return None


def cost(data: dict[str, Any], names: tuple[str, ...]) -> float:
    value = first(data, names)
    return abs(value) if value is not None else 0.0


def scope(account: str, symbol: str, broker: str = "") -> str:
    return SEP.join((text(account, DEFAULT_ACCOUNT), text(broker, "__unknown__"), text(symbol)))


def parts(key: str) -> tuple[str, str, str]:
    values = str(key).split(SEP, 2)
    return tuple(values) if len(values) == 3 else (DEFAULT_ACCOUNT, "__unknown__", str(key))


def position_scope(source: str, account: str, broker: str) -> str:
    return SEP.join((source, account, broker))


def normalize_position(raw: dict[str, Any], default_account: str, default_broker: str, source: str) -> dict[str, Any] | None:
    account = text(raw.get("account_id"), default_account)
    broker = text(raw.get("broker"), default_broker)
    symbol = text(raw.get("asset_canonical"), text(raw.get("symbol")))
    ticket = text(raw.get("ticket"), text(raw.get("broker_ticket")))
    direction = side(raw.get("side"))
    volume = num(raw.get("volume"))
    entry = num(raw.get("entry_price"))
    if not symbol or not ticket or direction is None or volume is None or entry is None:
        return None
    return {
        "ticket": ticket, "account_id": account, "broker": broker, "symbol": symbol, "side": direction,
        "volume": abs(volume), "entry_price": entry,
        "current_price": first(raw, ("current_price", "close_price", "price")),
        "bid": num(raw.get("bid")), "ask": num(raw.get("ask")),
        "profit": num(raw.get("profit")),
        "commission": cost(raw, ("commission_cost", "commission")),
        "swap": first(raw, ("swap", "swap_credit", "swap_cost")) or 0.0,
        "estimated_close_commission": cost(raw, ("estimated_close_commission", "est_close_commission")),
        "source_scope": position_scope(source, account, broker),
    }


def trade_key(data: dict[str, Any]) -> str:
    explicit = text(data.get("event_id"), text(data.get("source_row_id")))
    if explicit:
        return explicit
    return SEP.join(text(data.get(k)) for k in ("account_id", "ticket", "symbol", "close_time", "profit"))


def gross_realized(data: dict[str, Any]) -> float | None:
    """Broker realized profit before commission/swap.

    The extraction ladder is deliberately based on this value, never on
    floating P&L and never on an account-wide percentage.
    """
    gross = first(data, ("gross_pnl", "profit_gross", "gross_profit", "profit", "pnl"))
    if gross is not None:
        return gross
    return first(data, ("pnl", "profit_net", "net_profit"))


def realized(data: dict[str, Any]) -> float | None:
    direct = first(data, ("pnl", "profit_net", "net_profit"))
    if direct is not None:
        return direct
    gross = gross_realized(data)
    if gross is None:
        return None
    swap = first(data, ("swap", "swap_credit", "swap_cost")) or 0.0
    fee = cost(data, ("fee", "fee_cost"))
    return gross - cost(data, ("commission_cost", "commission")) + swap - fee


def spec_for(specs: dict[str, dict[str, Any]], account: str, broker: str, symbol: str) -> dict[str, Any] | None:
    direct = specs.get(scope(account, symbol, broker))
    if direct is not None:
        return direct
    # 2026-08-19: the account-scope fallback that lived here pre-multi-broker (before
    # "broker" became a third scope key) was dropped during that refactor and never
    # restored -- confirmed by comparing against the 2026-08-15 baseline snapshot. tick
    # size/value are properties of the broker+symbol pair, not the account, so a known
    # spec from any OTHER account on the SAME broker+symbol is a safe approximation while
    # this account's own spec hasn't arrived yet. Deliberately never falls back across
    # brokers -- tick specs can legitimately differ there and crossing that line would be
    # an invented number, not a restored one.
    suffix = SEP.join((text(broker, "__unknown__"), text(symbol)))
    for key, value in specs.items():
        if key.endswith(SEP + suffix):
            return value
    return None


def make_state(key: str, positions: dict[str, dict[str, Any]], realized_book: dict[str, float],
               extracted: dict[str, float], budgets: dict[str, float], default_budget: float,
               specs: dict[str, dict[str, Any]], count_realized: bool, decimals: int = 8,
               realized_gross_book: dict[str, float] | None = None,
               realized_costs_book: dict[str, float] | None = None) -> tuple[dict[str, Any], int]:
    account, broker, symbol = parts(key)
    legs = [p for p in positions.values() if scope(p["account_id"], p["symbol"], p.get("broker")) == key]
    warnings: list[str] = []
    details: list[dict[str, Any]] = []
    economic_total = 0.0
    cost_total = 0.0
    net_volume = 0.0
    weight = 0.0
    missing_specs = 0
    spec = spec_for(specs, account, broker, symbol)
    for position in legs:
        direction = position["side"]
        close = position.get("bid") if direction > 0 else position.get("ask")
        close = close if close is not None else position.get("current_price")
        floating = None
        calculation = "specification"
        if close is not None and spec is not None:
            floating = (close - position["entry_price"]) * direction * position["volume"] * (spec["tick_value"] / spec["tick_size"])
        if floating is None and position.get("profit") is not None:
            floating = position["profit"]
            calculation = "broker_profit_fallback"
            warnings.append("BROKER_PROFIT_FALLBACK")
        if floating is None:
            floating = 0.0
            warnings.append("MISSING_PRICE_OR_PROFIT")
        if spec is None:
            missing_specs += 1
            warnings.append("MISSING_SYMBOL_SPECS")
        leg_cost = position["commission"] + position["estimated_close_commission"] - position["swap"]
        economic = floating - leg_cost
        details.append({"ticket": position["ticket"], "symbol": symbol,
                        "side": "BUY" if direction > 0 else "SELL", "volume": position["volume"],
                        "entry_price": position["entry_price"], "close_price": close,
                        "floating": round(floating, decimals), "cost": round(leg_cost, decimals),
                        "economic": round(economic, decimals), "calculation": calculation})
        economic_total += economic
        cost_total += leg_cost
        net_volume += direction * position["volume"]
        weight += direction * position["volume"] * position["entry_price"]
    realized_total = realized_book.get(key, 0.0)
    gross_book = realized_gross_book if realized_gross_book is not None else realized_book
    realized_gross_total = gross_book.get(key, realized_total)
    extracted_total = extracted.get(key, 0.0)
    # The ladder uses gross realized profit, while retained economic credit
    # subtracts realized costs once.  Floating costs are in economic_total;
    # realized costs are not added there a second time.
    fallback_realized_cost = max(0.0, realized_gross_total - realized_total)
    realized_cost_total = (realized_costs_book or {}).get(key, fallback_realized_cost)
    credit_gross = max(0.0, realized_gross_total - extracted_total)
    credit = max(0.0, realized_total - extracted_total)
    net = credit + economic_total
    exposure = max(0.0, -net)
    budget = budgets.get(key, default_budget)
    # Owner's ruling 2026-08-13 (problem 1).  K keeps its constitution meaning
    # (article 13: K = max(0, G - X), never negative), so a realized LOSS used
    # to vanish before it could ever reach the guard: K clamped to 0, net = E,
    # u = 0, no warning, no breach -- a $315 loss against a $100 budget read as
    # untouched.  The loss now travels its OWN path to u.  The realized BALANCE
    # and the realized DRAWDOWN are two different quantities and are never
    # mixed: K is not touched, state_net is not touched, loss_exposure is not
    # touched, and u is simply the worse of the two ratios.
    realized_drawdown = max(0.0, -(realized_gross_total - extracted_total))
    u_float = exposure / budget if budget > 0 else None
    u_realized = realized_drawdown / budget if budget > 0 else None
    u = None if u_float is None else max(u_float, u_realized)
    unique = sorted(set(warnings))
    state = {
        "account_id": account, "broker": broker, "asset_canonical": symbol, "symbol": symbol,
        "risk_budget": round(budget, decimals), "R": round(budget, decimals),
        "budget": round(budget, decimals) if budget > 0 else None, "budgeted": budget > 0,
        "realized_net": round(realized_total, decimals), "realized_pnl": round(realized_total, decimals),
        "realized_gross": round(realized_gross_total, decimals), "gross_profit": round(realized_gross_total, decimals),
        "extracted": round(extracted_total, decimals), "K": round(credit, decimals), "K_gross": round(credit_gross, decimals), "X": round(extracted_total, decimals),
        "realized_costs": round(realized_cost_total, decimals), "buffer_k": round(credit, decimals), "floating_economic": round(economic_total, decimals),
        "floating": round(economic_total, decimals), "economic": round(economic_total, decimals),
        "cost": round(cost_total, decimals), "commission_est": round(cost_total, decimals),
        "net": round(net, decimals), "loss_exposure": round(exposure, decimals), "u": u,
        "realized_drawdown": round(realized_drawdown, decimals),
        "u_float": u_float, "u_realized": u_realized,
        "warning": u is not None and u >= 0.95, "breached": u is not None and u >= 1.0,
        "v_net": round(net_volume, decimals), "w": round(weight, decimals), "vpu": (
            spec["tick_value"] / spec["tick_size"] if spec else None),
        "position_count": len(legs), "open_legs": len(legs), "positions": details,
        "incomplete": bool(unique), "warnings": unique, "status": "ok" if budget > 0 and not unique else "degraded",
        "count_realized": count_realized,
    }
    return state, missing_specs
