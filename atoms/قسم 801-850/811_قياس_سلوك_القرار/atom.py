from __future__ import annotations

from collections import deque
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.analysis_speed import (horizon_value, limits_value, master_offset,
                                   speed_value)
from shared.decision_dials import effective_value

ATOM_VERSION = "1.1.0"

# Owner order 2026-08-25 (NQ): a BASELINE meter for decision behaviour.
# It listens to the score card and measures nothing but movement: how far each
# number jumped since the previous cycle, how often the side flips, how long a
# side survives, and how fast decisions arrive. It publishes measurement and
# NOTHING else -- no threshold, no dial, no verdict, no path into the decision.
# It lives in the measurement block on purpose: a reader must never mistake it
# for a participant in the decision it watches.
EVENT_IN = "decision.scored.state"
EVENT_OUT = "decision.behaviour.telemetry"
# Sealed expansion (two-axes paper v1.1 SS1 + owner's word "811", 2026-08-26):
# the meter now SEES the axes fields, the target, the delta and the budgets --
# still measurement only, still zero paths into the decision.
EVENT_TARGET = "perpetual.target.state"

_TARGET_FIELDS = ("risk_dial", "base_target", "target_gross", "target_net",
                  "current_gross", "current_net", "delta_buy", "delta_sell",
                  "allowed_increase", "decrease", "consumed_budget",
                  "remaining_RB", "dial_add_budget", "remaining_add_budget",
                  "action", "reason")
_GAP_WINDOW = 512

# The market stamp rides on the card. This atom is NOT one of the three clock
# owners and never reads a clock: every rate below is elapsed MARKET time.
_STAMP_FIELDS = ("source_timestamp", "period_start", "timestamp")

_TRACKED = ("direction", "strength", "confidence", "net", "participation")
_JUMP_FIELDS = ("direction", "strength", "confidence")
_SOURCE_FIELDS = {"direction": "direction_value", "strength": "strength_value",
                  "confidence": "confidence_value", "net": "net",
                  "participation": "participation"}

_WINDOW = 512
_RUN_WINDOW = 256
_MINUTE_S = 60.0
_DP = 4
_P25 = 0.25
_P50 = 0.50
_P75 = 0.75
_P90 = 0.90
_P95 = 0.95
_JUMP_QUANTILES = {"median": _P50, "p90": _P90, "p95": _P95}
_RUN_QUANTILES = {"median": _P50, "p25": _P25, "p75": _P75}

DIR_BUY = "buy"
DIR_SELL = "sell"
DIR_NEUTRAL = "neutral"
REVERSAL_DIRECT = "DIRECT_REVERSAL"
REVERSAL_AFTER_NEUTRAL = "REVERSAL_AFTER_NEUTRAL"
_TRANSITIONS = {(1, -1): "BUY_TO_SELL", (-1, 1): "SELL_TO_BUY",
                (1, 0): "BUY_TO_NEUTRAL", (-1, 0): "SELL_TO_NEUTRAL",
                (0, 1): "NEUTRAL_TO_BUY", (0, -1): "NEUTRAL_TO_SELL"}

IDENTITY_FIELDS = ("account_id", "broker", "symbol")
STATUS_OK = "ok"
ID_METER = "decision_behaviour"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _stats(values, quantiles: dict[str, float], with_mean: bool) -> dict[str, float]:
    """Nearest-rank quantiles over the sorted window. Nothing interpolated."""
    ordered = sorted(values)
    total = len(ordered)
    out: dict[str, float] = {"count": total}
    if with_mean:
        out["mean"] = round(sum(ordered) / total, _DP) if total else 0.0
    for name, quantile in quantiles.items():
        out[name] = (round(ordered[int(round(quantile * (total - 1)))], _DP)
                     if total else 0.0)
    out["max"] = round(ordered[-1], _DP) if total else 0.0
    return out


def _market_stamp(payload: dict[str, Any]) -> float | None:
    for field in _STAMP_FIELDS:
        stamp = _number(payload.get(field))
        if stamp is not None and stamp > 0:
            return stamp
    return None


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._books: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._targets: dict[tuple[str, str], dict[str, Any]] = {}
        self._seen = 0
        self._emitted = 0
        self._dropped = 0
        self._target_updates = 0
        self._drop_reasons: dict[str, int] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_scored)
        context.subscribe(EVENT_TARGET, self._on_target)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _book(self, key: tuple[str, str, str]) -> dict[str, Any]:
        book = self._books.get(key)
        if book is None:
            book = {"previous": None, "sign": 0, "last_side": 0, "run": 0,
                    "runs": deque(maxlen=_RUN_WINDOW),
                    "jumps": {field: deque(maxlen=_WINDOW) for field in _JUMP_FIELDS},
                    "gaps": deque(maxlen=_GAP_WINDOW),
                    "decisions": 0, "signals": 0, "ready": 0, "neutral": 0,
                    "direct": 0, "after_neutral": 0, "transitions": {},
                    "first_stamp": None, "last_stamp": None}
            self._books[key] = book
        return book

    def _drop(self, reason: str) -> None:
        self._dropped += 1
        self._drop_reasons[reason] = self._drop_reasons.get(reason, 0) + 1

    async def _on_target(self, payload: dict[str, Any]) -> None:
        """Latest 581 target/budget snapshot per (account, symbol) -- the
        target event carries no broker, so the pair is the join key."""
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        snap = {field: payload.get(field) for field in _TARGET_FIELDS
                if payload.get(field) is not None}
        self._targets[(str(payload.get("account_id") or ""), symbol)] = snap
        self._target_updates += 1

    async def _on_scored(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        identity = {field: payload.get(field) for field in IDENTITY_FIELDS}
        symbol = str(identity.get("symbol") or "")
        if not symbol:
            self._drop("IDENTITY_MISSING")
            return
        current: dict[str, float] = {}
        for field in _TRACKED:
            value = _number(payload.get(_SOURCE_FIELDS[field]))
            if value is None:
                self._drop("FIELD_MISSING:%s" % field)
                return
            current[field] = value
        self._seen += 1

        key = (str(identity.get("account_id") or ""),
               str(identity.get("broker") or ""), symbol)
        book = self._book(key)
        previous = book["previous"]
        direction = str(payload.get("direction") or payload.get("signal") or "")
        complete = payload.get("complete") is True

        book["decisions"] += 1
        if direction == DIR_NEUTRAL:
            book["neutral"] += 1
        else:
            book["signals"] += 1
        if complete:
            book["ready"] += 1
        stamp = _market_stamp(payload)
        if stamp is not None:
            if book["first_stamp"] is None:
                book["first_stamp"] = stamp
            if book["last_stamp"] is not None and stamp > book["last_stamp"]:
                # Market-clock gap between consecutive score cards -- the
                # honest decision cadence (owner's rule: never the wall clock).
                book["gaps"].append(stamp - book["last_stamp"])
            book["last_stamp"] = stamp

        moves: dict[str, Any] = {}
        for field in _TRACKED:
            before = None if previous is None else previous[field]
            delta = None if before is None else round(current[field] - before, _DP)
            moves[field] = {"previous": before, "current": current[field],
                            "delta": delta}
            if field in _JUMP_FIELDS and delta is not None:
                book["jumps"][field].append(abs(delta))

        sign = 1 if direction == DIR_BUY else -1 if direction == DIR_SELL else 0
        was = book["sign"]
        transition = reversal = None
        if previous is not None and sign != was:
            transition = _TRANSITIONS.get((was, sign))
            if transition is not None:
                book["transitions"][transition] = \
                    book["transitions"].get(transition, 0) + 1
            if was and sign:
                reversal = REVERSAL_DIRECT
                book["direct"] += 1
            elif sign and book["last_side"] and book["last_side"] != sign:
                reversal = REVERSAL_AFTER_NEUTRAL
                book["after_neutral"] += 1
            book["runs"].append(book["run"])
            book["run"] = 1
        else:
            book["run"] += 1
        if sign:
            book["last_side"] = sign
        book["sign"] = sign
        book["previous"] = dict(current)

        # The four owner keys + the risk dial, read live at record time --
        # provenance for every row (papers: speed SS23, unified SS68, v1.1 SS1).
        account = key[0]
        keys_now = {
            "master_shift": round(master_offset(account, symbol), 2),
            "speed": round(speed_value(account, symbol), 2),
            "horizon": round(horizon_value(account, symbol), 2),
            "limits": round(limits_value(account, symbol), 2),
            "risk_dial": round(effective_value("RISK_DIAL", 100.0), 2),
        }
        await self._context.publish(EVENT_OUT, {
            **identity, "id": ID_METER, "status": STATUS_OK,
            "cycle_id": str(payload.get("cycle_id") or ""),
            "market_stamp": stamp, "measured_from": "market_stamp",
            "signal": direction, "run_length": book["run"],
            "transition": transition, "reversal_kind": reversal,
            "keys": keys_now,
            "target": self._targets.get((account, symbol)),
            "window": _WINDOW, **moves, **self._report(book)})
        self._emitted += 1

    def _report(self, book: dict[str, Any]) -> dict[str, Any]:
        runs = list(book["runs"]) + ([book["run"]] if book["run"] else [])
        first, last = book["first_stamp"], book["last_stamp"]
        elapsed = (max(0.0, last - first)
                   if first is not None and last is not None else 0.0)
        counts = {"decisions": book["decisions"], "signals": book["signals"],
                  "ready": book["ready"], "neutral": book["neutral"],
                  "reversals": book["direct"] + book["after_neutral"]}
        return {
            "jump_stats": {field: _stats(book["jumps"][field], _JUMP_QUANTILES, True)
                           for field in _JUMP_FIELDS},
            "run_stats": _stats(runs, _RUN_QUANTILES, False),
            "cadence_gap_s": _stats(book["gaps"], _JUMP_QUANTILES, True),
            "counts": {**counts, "direct_reversals": book["direct"],
                       "reversals_after_neutral": book["after_neutral"]},
            "transitions": dict(book["transitions"]),
            "rates_per_minute": ({name: round(total / elapsed * _MINUTE_S, _DP)
                                  for name, total in counts.items()} if elapsed > 0
                                 else {name: None for name in counts}),
            # Owner's silence rule (scalping SS26/SS48): zero jumps beside zero
            # signals is NOT success -- flagged, never hidden inside a mean.
            "all_neutral": bool(book["decisions"] > 0 and book["signals"] == 0),
            "elapsed_market_s": round(elapsed, _DP)}

    async def snapshot(self) -> dict[str, Any]:
        return {"targets": [{"key": list(key), "snap": snap}
                            for key, snap in self._targets.items()],
                "target_updates": self._target_updates,
                "books": [{"key": list(key), "previous": book["previous"],
                           "sign": book["sign"], "last_side": book["last_side"],
                           "run": book["run"], "runs": list(book["runs"]),
                           "gaps": list(book["gaps"]),
                           "jumps": {f: list(d) for f, d in book["jumps"].items()},
                           "decisions": book["decisions"], "signals": book["signals"],
                           "ready": book["ready"], "neutral": book["neutral"],
                           "direct": book["direct"],
                           "after_neutral": book["after_neutral"],
                           "transitions": book["transitions"],
                           "first_stamp": book["first_stamp"],
                           "last_stamp": book["last_stamp"]}
                          for key, book in self._books.items()],
                "seen": self._seen, "emitted": self._emitted,
                "dropped": self._dropped, "drop_reasons": self._drop_reasons}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        self._seen = int(state.get("seen") or 0)
        self._emitted = int(state.get("emitted") or 0)
        self._dropped = int(state.get("dropped") or 0)
        reasons = state.get("drop_reasons")
        self._drop_reasons = dict(reasons) if isinstance(reasons, dict) else {}
        self._target_updates = int(state.get("target_updates") or 0)
        self._targets = {}
        for row in state.get("targets") or []:
            if (isinstance(row, dict) and isinstance(row.get("key"), list)
                    and len(row["key"]) == 2 and isinstance(row.get("snap"), dict)):
                self._targets[(str(row["key"][0]), str(row["key"][1]))] = dict(row["snap"])
        self._books = {}
        for row in state.get("books") or []:
            if not isinstance(row, dict) or not isinstance(row.get("key"), list):
                continue
            key = tuple(str(part) for part in row["key"])
            if len(key) != len(IDENTITY_FIELDS):
                continue
            book = self._book(key)
            book["previous"] = row.get("previous")
            book["sign"] = int(row.get("sign") or 0)
            book["last_side"] = int(row.get("last_side") or 0)
            book["run"] = int(row.get("run") or 0)
            book["runs"].extend(row.get("runs") or [])
            book["gaps"].extend(value for value in (row.get("gaps") or [])
                                if _number(value) is not None)
            for field in _JUMP_FIELDS:
                book["jumps"][field].extend((row.get("jumps") or {}).get(field) or [])
            for name in ("decisions", "signals", "ready", "neutral",
                         "direct", "after_neutral"):
                book[name] = int(row.get(name) or 0)
            transitions = row.get("transitions")
            book["transitions"] = dict(transitions) if isinstance(transitions, dict) else {}
            book["first_stamp"] = _number(row.get("first_stamp"))
            book["last_stamp"] = _number(row.get("last_stamp"))

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "emitted": self._emitted,
                   "dropped": self._dropped, "drop_reasons": dict(self._drop_reasons),
                   "scopes": len(self._books), "target_updates": self._target_updates,
                   "books": {"|".join(key): self._report(book)
                             for key, book in self._books.items()}}
        if not self._seen:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT,
                                details=details)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d emitted=%d scopes=%d dropped=%d" % (
                self._seen, self._emitted, len(self._books), self._dropped),
            details=details)
