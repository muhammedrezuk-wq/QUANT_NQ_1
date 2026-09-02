"""Canonical adapter for the validated tick path used by sections 350-368.

Source path: 622 feed.ctrader.tick -> 613 market.tick ->
112 market.tick.validated.  No atom in the model section binds to cTrader or
MT5 directly; this adapter only gives the tick a deterministic cycle identity
and compatibility price fields.
"""

from __future__ import annotations

from typing import Any, Mapping

from shared.cycle_identity import cycle_key_of

VALIDATED_TICK_EVENT = "market.tick.validated"
TICK_TIMEFRAME = "tick"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number_text(value: Any) -> str:
    try:
        return format(float(value), ".15g")
    except (TypeError, ValueError):
        return _text(value)


def as_validated_tick(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Return an isolated tick payload with one stable model-cycle identity.

    Existing candle-shaped payloads remain accepted by unit tests/migrations,
    but live subscriptions consume only ``market.tick.validated``.
    """
    source = dict(payload) if isinstance(payload, Mapping) else {}
    price = source.get("price")
    if price is None:
        price = source.get("close")
    if price is None:
        try:
            price = (float(source.get("bid")) + float(source.get("ask"))) / 2.0
        except (TypeError, ValueError):
            price = None
    source["price"] = price
    source.setdefault("close", price)
    source["timeframe"] = _text(source.get("timeframe")) or TICK_TIMEFRAME
    stamp = source.get(
        "period_start", source.get("exchange_timestamp", source.get("timestamp"))
    )
    explicit = source.get("tick_id") or source.get("sequence") or source.get("event_id")
    # Quote-change identity is deterministic across every subscriber.  Bid/ask
    # disambiguate venues that stamp multiple changes at the same instant.
    if explicit:
        tick_identity = _text(explicit)
    elif source.get("period_start") is not None:
        # Compatibility for recorded candle fixtures during migration.
        tick_identity = _text(source.get("period_start"))
    else:
        tick_identity = "%s@%s:%s" % (
            _number_text(stamp),
            _number_text(source.get("bid")),
            _number_text(source.get("ask")),
        )
    source["tick_id"] = tick_identity
    source["period_start"] = tick_identity
    source.setdefault("source_timestamp", stamp)
    source["cycle_id"] = cycle_key_of(
        source,
        symbol=source.get("symbol"),
        timeframe=source["timeframe"],
        period_start=tick_identity,
    )
    return source
