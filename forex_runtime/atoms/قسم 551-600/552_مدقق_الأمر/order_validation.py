from __future__ import annotations
from typing import Any

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
ACTION_OPEN = "OPEN"
ACTION_CLOSE = "CLOSE"
ACTION_CLOSE_PARTIAL = "CLOSE_PARTIAL"
ACTION_MODIFY_SL = "MODIFY_SL"
ACTION_MODIFY_TP = "MODIFY_TP"
PROTECTION_NEUTRAL_HEDGE = "NEUTRAL_HEDGE"
PROTECTION_PERPETUAL = "PERPETUAL_BUDGET"
ORIGIN_PERPETUAL = "perpetual-delta"

def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _neutral_pair_contract(order: dict[str, Any]) -> bool:
    """Return true only for the explicitly defined neutral-pair exception.

    A missing stop is never accepted just because a caller says it is a hedge.
    The request must carry a pair identity, a role, an attempt number and the
    protection mode.  The broker EA applies the same check before sending the
    order, so this exception does not turn the normal stop guard off.
    """
    mode = str(order.get("protection_mode") or "").upper()
    role = str(order.get("leg_role") or "").upper()
    pair_id = str(order.get("pair_id") or "").strip()
    attempt = _to_float(order.get("attempt"))
    required = order.get("pair_required") is True
    return (
        str(order.get("action") or ACTION_OPEN).upper() == ACTION_OPEN
        and mode == PROTECTION_NEUTRAL_HEDGE
        and required
        and bool(pair_id)
        and role in (SIDE_BUY, SIDE_SELL)
        and attempt is not None and attempt >= 1
        and order.get("stop_loss") in (None, 0, 0.0)
        and order.get("take_profit") in (None, 0, 0.0)
    )


def _perpetual_budget_contract(order: dict[str, Any]) -> str | None:
    """The second, explicitly defined exception -- now a NO-TAKE-PROFIT one.

    Owner's ruling 2026-08-13 (problem 2, option c): the asset budget
    (512/518/519) stays the risk MANAGER, and the broker stop comes back as a
    LAST RESORT.  So a perpetual leg is no longer allowed through without a
    stop; what it is excused from is the TAKE PROFIT, because a perpetual
    position is never closed by a target -- profit leaves through extraction.

    Returns None when the order is not a perpetual leg at all (fall through to
    the normal rules), "" when it is a valid one, or a rejection reason.  The
    shape stays as narrow as the neutral-pair exception: declared mode, the
    perpetual origin, a positive budget the guard can be held to, an empty
    target, and a real stop on the correct side of the price.
    """
    if not (str(order.get("action") or ACTION_OPEN).upper() == ACTION_OPEN
            and str(order.get("protection_mode") or "").upper() == PROTECTION_PERPETUAL
            and str(order.get("origin") or "") == ORIGIN_PERPETUAL):
        return None
    if (_to_float(order.get("risk_budget")) or 0.0) <= 0.0:
        return "NO_BUDGET"
    if order.get("take_profit") not in (None, 0, 0.0):
        return "PERPETUAL_NO_TP"
    stop = _to_float(order.get("stop_loss"))
    ref = _to_float(order.get("reference_price"))
    if stop is None or stop <= 0.0:
        return "NO_STOP"
    side = str(order.get("side", "")).upper()
    if ref is None or ref <= 0.0:
        return "BAD_PRICE"
    if side == SIDE_BUY and not stop < ref:
        return "BUY_LEVELS"
    if side == SIDE_SELL and not stop > ref:
        return "SELL_LEVELS"
    return ""


def _validate(order: dict[str, Any]) -> str:
    symbol = order.get("symbol")
    action = str(order.get("action") or ACTION_OPEN).upper()
    side = str(order.get("side", "")).upper()
    ref = _to_float(order.get("reference_price"))
    stop = _to_float(order.get("stop_loss"))
    target = _to_float(order.get("take_profit"))
    volume = _to_float(order.get("volume"))
    if action not in (ACTION_OPEN, ACTION_CLOSE, ACTION_CLOSE_PARTIAL, ACTION_MODIFY_SL, ACTION_MODIFY_TP):
        return "BAD_ACTION"
    if not symbol:
        return "NO_SYMBOL"
    if side not in (SIDE_BUY, SIDE_SELL):
        return "BAD_SIDE"
    if volume is None or volume <= 0.0:
        return "BAD_VOLUME"
    if action != ACTION_OPEN:
        return "" if order.get("ticket") not in (None, "", 0) else "NO_TICKET"
    if ref is None or ref <= 0.0:
        return "BAD_PRICE"

    perpetual = _perpetual_budget_contract(order)
    if perpetual is not None:
        return perpetual
    if _neutral_pair_contract(order):
        return ""
    if stop is None or stop <= 0.0:
        return "NO_STOP"
    if target is None or target <= 0.0:
        return "NO_TARGET"
    if side == SIDE_BUY and not (stop < ref < target):
        return "BUY_LEVELS"
    if side == SIDE_SELL and not (target < ref < stop):
        return "SELL_LEVELS"
    return ""

