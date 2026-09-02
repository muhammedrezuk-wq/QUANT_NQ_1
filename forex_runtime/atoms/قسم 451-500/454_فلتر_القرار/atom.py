from __future__ import annotations

import time
from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.decision_dials import (EVENT_COMMAND as EVENT_DIALS_COMMAND,
                                   EVENT_STATE as EVENT_DIALS_STATE,
                                   apply_command, effective_value)

ATOM_VERSION = "2.5.0"

EVENT_IN = "decision.resolved.state"
EVENT_OUT = "decision.filtered.state"
EVENT_CALENDAR = "market_data.calendar.state"
EVENT_QUALITY = "market_data.quality.state"
EVENT_FEED = "market_data.feed.state"
# Owner item 22 / batch A (ruling Q7): per-symbol news trading windows from
# 411. block=true inside the window bars the decision under the barrier name
# "news_window" — same family as the calendar barrier.
EVENT_NEWS_WINDOW = "news.trading_window.state"
BARRIER_NEWS_WINDOW = "news_window"

_FILTER_EVENTS = (
    "decision.filter.confidence.state",
    "decision.filter.conditions.state",
    "decision.filter.timing.state",
    "decision.filter.position.state",
    "decision.filter.freshness.state",
    "decision.filter.asset.state",
)
_FILTER_IDS = (
    "confidence_filter", "conditions_filter", "timing_filter",
    "position_filter", "freshness_filter", "asset_filter",
)
_CYCLE_FILTER_IDS = ("confidence_filter", "conditions_filter", "timing_filter", "asset_filter")
_SYMBOL_FILTER_IDS = ("position_filter", "freshness_filter")

METHOD = "score_gate_and_complete_fresh_filters"
ID_FILTER = "decision_filter"
DIR_WAIT = "wait"
STATUS_OK = "ok"
QUALITY_GOOD = "good"
QUALITY_LOW = "low"
REASON_NOT_STARTED = "NOT_STARTED"
REASON_NO_INPUT = "NO_INPUT_YET"
DEFAULT_FILTER_TTL_S = 30.0
MIN_FILTER_TTL_S = 0.1

# NQ seal item 22 batch B (B6): the decision side is its own vocabulary --
# decision_side in {"buy","sell","wait"} only, never mixed with the +-100
# directional value. Legacy payloads without decision_side fall back to the
# word in "signal"; legacy "neutral" means no side (-> wait); anything else
# is UNKNOWN and blocks explicitly -- an unknown is not a known wait.
SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDE_WAIT = "wait"
_LEGACY_NEUTRAL = "neutral"

# B1 (ruling Q9 s17): the six-field decision identity crosses this hop
# complete; a missing field is republished None (never invented) under the
# "identity_incomplete" warning with its name.
IDENTITY_FIELDS = ("account_id", "broker", "symbol", "timeframe",
                   "period_start", "decision_id")
WARN_IDENTITY_INCOMPLETE = "identity_incomplete"

# B7 barrier reason codes -- every barrier this atom declares carries the four
# fields {value, threshold, reason, measured_at}. value/threshold are None
# where 454 genuinely has no measurement/threshold for them (never invented).
REASON_SIDE_UNKNOWN = "DECISION_SIDE_UNKNOWN"
REASON_SIDE_WAIT = "DECISION_SIDE_WAIT"
REASON_SCORE_UNKNOWN = "SCORE_UNKNOWN"
REASON_SCORE_BELOW_MIN = "SCORE_BELOW_MIN"
REASON_FILTER_MISSING = "FILTER_VERDICT_MISSING"
REASON_FILTER_STALE = "FILTER_VERDICT_STALE"
REASON_FILTER_MISMATCH = "FILTER_CYCLE_MISMATCH"
REASON_FILTER_FAILED = "FILTER_FAILED"
REASON_CALENDAR_UNKNOWN = "CALENDAR_UNKNOWN"
REASON_CALENDAR_WINDOW = "CALENDAR_EVENT_WINDOW"
REASON_NEWS_WINDOW = "NEWS_WINDOW_BLOCK"
REASON_QUALITY_UNKNOWN = "MARKET_QUALITY_UNKNOWN"
REASON_QUALITY_INVALID = "MARKET_QUALITY_INVALID"
REASON_FEED_NOT_ACTIVE = "FEED_NOT_ACTIVE"


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _identity_of(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    identity: dict[str, Any] = {}
    missing: list[str] = []
    for field in IDENTITY_FIELDS:
        value = payload.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            identity[field] = None
            missing.append(field)
        else:
            identity[field] = value
    return identity, missing


def _side_of(payload: dict[str, Any]) -> str | None:
    side = str(payload.get("decision_side") or "").strip().lower()
    if side in (SIDE_BUY, SIDE_SELL, SIDE_WAIT):
        return side
    legacy = str(payload.get("signal") or "").strip().lower()
    if legacy in (SIDE_BUY, SIDE_SELL, SIDE_WAIT):
        return legacy
    if legacy == _LEGACY_NEUTRAL:
        return SIDE_WAIT
    return None


def _barrier(name: str, value: Any, threshold: Any, reason: str,
             measured_at: float | None) -> dict[str, Any]:
    return {"name": name, "value": value, "threshold": threshold,
            "reason": reason, "measured_at": measured_at}


class Atom(AtomBase):

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._min_score = 60
        self._filter_ttl_s = DEFAULT_FILTER_TTL_S
        self._filters: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
        self._symbol_filters: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._seen = 0
        self._passed = 0
        self._emitted = 0
        self._missing = 0
        self._stale = 0
        self._cycle_mismatch = 0
        self._last_blocked_by: list[str] = []
        self._last_buckets: dict[str, list[str]] = {}
        self._dials_applied = 0
        self._calendar_known = False
        self._calendar_blocked = False
        self._calendar_measured_at: float | None = None
        self._market_quality: dict[tuple[str, str], dict[str, Any]] = {}
        self._feed_status = "UNKNOWN"
        self._feed_measured_at: float | None = None
        self._news_windows: dict[str, dict[str, dict[str, Any]]] = {}

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        self._min_score = int(effective_value(
            "DECISION_MIN_SCORE", float(context.config["min_score"])))
        self._filter_ttl_s = max(MIN_FILTER_TTL_S, effective_value(
            "DECISION_FILTER_TTL_S",
            float(context.config.get("filter_ttl_s", DEFAULT_FILTER_TTL_S))))
        self._dials_applied = 0
        context.subscribe(EVENT_DIALS_COMMAND, self._on_dial_command)
        context.subscribe(EVENT_IN, self._on_scored)
        for event in _FILTER_EVENTS:
            context.subscribe(event, self._on_filter)
        context.subscribe(EVENT_CALENDAR, self._on_calendar)
        context.subscribe(EVENT_QUALITY, self._on_quality)
        context.subscribe(EVENT_FEED, self._on_feed)
        context.subscribe(EVENT_NEWS_WINDOW, self._on_news_window)

    _DIAL_ATTRS = {"DECISION_MIN_SCORE": "_min_score",
                   "DECISION_FILTER_TTL_S": "_filter_ttl_s"}

    async def _on_dial_command(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None:
            return
        applied = apply_command(payload, atom_id="454")
        if applied is None:
            return
        value = float(applied["value"])
        if applied["name"] == "DECISION_MIN_SCORE":
            self._min_score = int(value)
        else:
            self._filter_ttl_s = max(MIN_FILTER_TTL_S, value)
        self._dials_applied += 1
        await self._publish_dials_state()

    async def _publish_dials_state(self) -> None:
        if self._context is None:
            return
        await self._context.publish(EVENT_DIALS_STATE, {
            "id": "decision_dials_454", "atom_id": "454", "status": STATUS_OK,
            "dials": {"DECISION_MIN_SCORE": float(self._min_score),
                      "DECISION_FILTER_TTL_S": self._filter_ttl_s}})

    async def start(self) -> None:
        self._running = True
        await self._publish_dials_state()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_filter(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        fid = str(payload.get("id") or "")
        if fid not in _FILTER_IDS:
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        account = str(payload.get("account_id") or "*")
        timeframe = str(payload.get("timeframe") or "")
        cycle = str(payload.get("cycle_id") or "")
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        record = {
            "passed": meta.get("passed") is True,
            "cycle_id": cycle,
            "seen_at": time.monotonic(),
            # B7: wall-clock measurement time for the barrier contract
            # (seen_at stays monotonic for the TTL arithmetic only).
            "measured_at": time.time(),
        }
        if fid in _SYMBOL_FILTER_IDS:
            self._symbol_filters.setdefault((account, symbol), {})[fid] = record
        else:
            bucket = self._filters.setdefault(
                (account, symbol, timeframe), {}).setdefault(fid, {})
            bucket[cycle] = record
            now = record["seen_at"]
            for old_cycle in [c for c, r in bucket.items()
                              if now - float(r.get("seen_at") or 0.0) > self._filter_ttl_s]:
                del bucket[old_cycle]

    async def _on_calendar(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        self._calendar_known = payload.get("known") is True
        self._calendar_blocked = payload.get("in_event_window") is True
        self._calendar_measured_at = time.time()

    async def _on_news_window(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        window_end = _to_float(payload.get("window_end"))
        if not symbol or window_end is None:
            return
        record = {
            "block": payload.get("block") is True,
            "grade": str(payload.get("grade") or ""),
            "phase": str(payload.get("phase") or ""),
            "window_start": _to_float(payload.get("window_start")),
            "window_end": window_end,
            "headline": str(payload.get("headline") or ""),
            "measured_at": time.time(),
        }
        bucket = self._news_windows.setdefault(symbol, {})
        bucket["%s|%s" % (record["headline"], window_end)] = record
        now = time.time()
        for key in [k for k, r in bucket.items() if now > float(r["window_end"])]:
            del bucket[key]

    def _active_news_block(self, symbol: str) -> dict[str, Any] | None:
        bucket = self._news_windows.get(symbol)
        if not bucket:
            return None
        now = time.time()
        active: dict[str, Any] | None = None
        for key in list(bucket):
            record = bucket[key]
            end = float(record["window_end"])
            if now > end:
                del bucket[key]
                continue
            start = record["window_start"]
            if start is not None and now < float(start):
                continue
            if record["block"] and (active is None
                                    or end > float(active["window_end"])):
                active = record
        return active

    async def _on_quality(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "*")
        account = str(payload.get("account_id") or "*")
        status = str(payload.get("status") or "UNKNOWN").upper()
        self._market_quality[(account, symbol)] = {
            "status": status, "measured_at": time.time()}

    async def _on_feed(self, payload: dict[str, Any]) -> None:
        if self._running and isinstance(payload, dict):
            self._feed_status = str(payload.get("status") or "UNKNOWN").upper()
            self._feed_measured_at = time.time()

    _QUALITY_RANK = {"HEALTHY": 0, "DEGRADED": 1, "UNKNOWN": 2, "INVALID": 3}

    def _quality_for(self, account: str, symbol: str) -> tuple[str, float | None]:
        for key in ((account, symbol), ("*", symbol), (account, "*"), ("*", "*")):
            if key in self._market_quality:
                record = self._market_quality[key]
                return record["status"], record.get("measured_at")
        worst: dict[str, Any] | None = None
        for (_stored_account, stored_symbol), record in self._market_quality.items():
            if stored_symbol == symbol:
                status = record["status"]
                if worst is None or (self._QUALITY_RANK.get(status, 2)
                                     > self._QUALITY_RANK.get(worst["status"], 2)):
                    worst = record
        if worst is None:
            return "UNKNOWN", None
        return worst["status"], worst.get("measured_at")

    def _cycle_buckets(self, account: str, symbol: str, timeframe: str) -> list[dict[str, dict[str, dict[str, Any]]]]:
        buckets: list[dict[str, dict[str, dict[str, Any]]]] = []
        for (stored_account, stored_symbol, stored_tf), recs in self._filters.items():
            if stored_symbol == symbol and stored_tf == timeframe \
                    and stored_account not in ("*", account):
                buckets.append(recs)
        for key in (("*", symbol, timeframe), (account, symbol, timeframe)):
            if key in self._filters:
                buckets.append(self._filters[key])
        return buckets

    def _symbol_records(self, account: str, symbol: str) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for (stored_account, stored_symbol), recs in self._symbol_filters.items():
            if stored_symbol == symbol and stored_account not in ("*", account):
                records.update(recs)
        records.update(self._symbol_filters.get(("*", symbol), {}))
        records.update(self._symbol_filters.get((account, symbol), {}))
        return records

    async def _on_scored(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        if not symbol:
            return
        account = str(payload.get("account_id") or "*")
        timeframe = str(payload.get("timeframe") or "")
        cycle_id = str(payload.get("cycle_id") or "")
        identity, identity_missing = _identity_of(payload)
        side = _side_of(payload)
        score_value = _to_float(payload.get("score"))
        now_wall = time.time()
        barriers: dict[str, dict[str, Any]] = {}
        # --- decision gate (B6): an unknown side/score is not a known
        # wait/zero -- it blocks under its own explicit barrier instead of
        # slipping through (or being stopped by) a fake comparison.
        gate_blocks: list[str] = []
        if side is None:
            gate_blocks.append("decision_side_unknown")
            barriers["decision_side_unknown"] = _barrier(
                "decision_side_unknown", None, None, REASON_SIDE_UNKNOWN, now_wall)
        elif side == SIDE_WAIT:
            # Legacy blocker name kept (compatibility with dashboards/logs).
            gate_blocks.append("signal_wait")
            barriers["signal_wait"] = _barrier(
                "signal_wait", side, None, REASON_SIDE_WAIT, now_wall)
        elif score_value is None:
            gate_blocks.append("score_unknown")
            barriers["score_unknown"] = _barrier(
                "score_unknown", None, float(self._min_score),
                REASON_SCORE_UNKNOWN, now_wall)
        elif score_value < self._min_score:
            gate_blocks.append("score_gate")
            barriers["score_gate"] = _barrier(
                "score_gate", score_value, float(self._min_score),
                REASON_SCORE_BELOW_MIN, now_wall)
        gate = not gate_blocks
        now = time.monotonic()
        chosen: dict[str, dict[str, Any]] = {}
        seen_fids: set[str] = set()
        for bucket in self._cycle_buckets(account, symbol, timeframe):
            for fid, by_cycle in bucket.items():
                if fid not in _CYCLE_FILTER_IDS or not isinstance(by_cycle, dict):
                    continue
                seen_fids.add(fid)
                # v2.5.0 (2026-08-25): a filter verdict is evidence about the
                # SCOPE with an age -- not about one ~200ms tick cycle. The
                # exact-cycle equality demanded a verdict for THIS cycle id
                # while cycles churn per tick, so verdicts were perpetually
                # "one cycle old" and FILTER_CYCLE_MISMATCH became a top
                # measured blocker. The freshest verdict inside the owner's
                # DECISION_FILTER_TTL_S dial now applies; its own cycle id is
                # declared (verdict_cycles metadata) and staleness still
                # blocks via REASON_FILTER_STALE.
                record = by_cycle.get(cycle_id) if cycle_id else None
                if record is None and by_cycle:
                    record = max(by_cycle.values(),
                                 key=lambda r: float(r.get("seen_at") or 0.0))
                if record is not None:
                    chosen[fid] = record
        for fid, record in self._symbol_records(account, symbol).items():
            if fid in _SYMBOL_FILTER_IDS:
                chosen[fid] = record
                seen_fids.add(fid)
        missing = [fid for fid in _FILTER_IDS
                   if fid not in chosen and fid not in seen_fids]
        cycle_mismatch = [fid for fid in _CYCLE_FILTER_IDS
                          if fid not in chosen and fid in seen_fids]
        stale = [fid for fid, rec in chosen.items()
                 if now - float(rec.get("seen_at") or 0.0) > self._filter_ttl_s]
        failed = [fid for fid, rec in chosen.items() if rec.get("passed") is not True]
        # B7: one quad row per blocking barrier, first classification wins --
        # same order the blocked_by dedup uses below.
        for fid in missing:
            barriers.setdefault(fid, _barrier(
                fid, None, None, REASON_FILTER_MISSING, None))
        for fid in stale:
            record = chosen.get(fid) or {}
            age = now - float(record.get("seen_at") or 0.0)
            barriers.setdefault(fid, _barrier(
                fid, round(age, 3), self._filter_ttl_s, REASON_FILTER_STALE,
                record.get("measured_at")))
        for fid in cycle_mismatch:
            barriers.setdefault(fid, _barrier(
                fid, None, None, REASON_FILTER_MISMATCH, None))
        for fid in failed:
            record = chosen.get(fid) or {}
            barriers.setdefault(fid, _barrier(
                fid, False, None, REASON_FILTER_FAILED, record.get("measured_at")))
        operational: list[str] = []
        if not self._calendar_known:
            operational.append("calendar_unknown")
            barriers.setdefault("calendar_unknown", _barrier(
                "calendar_unknown", None, None, REASON_CALENDAR_UNKNOWN,
                self._calendar_measured_at))
        elif self._calendar_blocked:
            operational.append("calendar_window")
            barriers.setdefault("calendar_window", _barrier(
                "calendar_window", True, None, REASON_CALENDAR_WINDOW,
                self._calendar_measured_at))
        news_block = self._active_news_block(symbol)
        if news_block is not None:
            operational.append(BARRIER_NEWS_WINDOW)
            barriers.setdefault(BARRIER_NEWS_WINDOW, _barrier(
                BARRIER_NEWS_WINDOW, news_block["grade"] or None, None,
                REASON_NEWS_WINDOW, news_block.get("measured_at")))
        market_quality, quality_measured_at = self._quality_for(account, symbol)
        if market_quality == "UNKNOWN":
            operational.append("market_quality_unknown")
            barriers.setdefault("market_quality_unknown", _barrier(
                "market_quality_unknown", None, None, REASON_QUALITY_UNKNOWN,
                quality_measured_at))
        elif market_quality == "INVALID":
            operational.append("market_quality_invalid")
            barriers.setdefault("market_quality_invalid", _barrier(
                "market_quality_invalid", market_quality, None,
                REASON_QUALITY_INVALID, quality_measured_at))
        if self._feed_status != "ACTIVE":
            feed_name = "feed_" + self._feed_status.lower()
            operational.append(feed_name)
            barriers.setdefault(feed_name, _barrier(
                feed_name, self._feed_status, "ACTIVE", REASON_FEED_NOT_ACTIVE,
                self._feed_measured_at))
        blocked_by = list(dict.fromkeys(
            gate_blocks + missing + stale + cycle_mismatch + failed + operational))
        passed = gate and not blocked_by
        barrier_rows = [barriers[name] for name in blocked_by if name in barriers]
        self._last_blocked_by = blocked_by
        self._last_buckets = {"gate": gate_blocks, "miss": missing, "stale": stale,
                              "mism": cycle_mismatch, "fail": failed, "op": operational}
        self._seen += 1
        self._missing += len(missing)
        self._stale += len(stale)
        self._cycle_mismatch += len(cycle_mismatch)
        if passed:
            self._passed += 1
        warnings = list(blocked_by)
        if identity_missing:
            warnings.append(WARN_IDENTITY_INCOMPLETE)
        await self._context.publish(EVENT_OUT, {
            **payload, **identity,
            "symbol": symbol,
            "id": ID_FILTER, "cycle_id": cycle_id, "status": STATUS_OK,
            "identity_missing": identity_missing,
            "decision_side": side,
            # Honest score: the measured value or None -- never a coerced 0.
            "score": score_value,
            "signal": str(payload.get("signal") or DIR_WAIT),
            "confidence": 1.0 if passed else 0.0,
            "quality": QUALITY_GOOD if passed else QUALITY_LOW,
            "warnings": warnings,
            "barriers": barrier_rows,
            "metadata": {
                "method": METHOD, "timeframe": timeframe,
                "direction": str(payload.get("signal") or DIR_WAIT),
                "decision_side": side,
                "passed": passed, "score_gate": gate, "blocked_by": blocked_by,
                "missing_filters": missing, "stale_filters": stale,
                "cycle_mismatch": cycle_mismatch, "min_score": self._min_score,
                "verdict_cycles": {fid: str(rec.get("cycle_id") or "")
                                   for fid, rec in chosen.items()},
                "filter_ttl_s": self._filter_ttl_s,
                "calendar_known": self._calendar_known,
                "calendar_blocked": self._calendar_blocked,
                "news_window_blocked": news_block is not None,
                "news_window": ({"grade": news_block["grade"],
                                 "phase": news_block["phase"],
                                 "window_end": news_block["window_end"]}
                                if news_block is not None else None),
                "market_quality": market_quality,
                "feed_status": self._feed_status,
            },
        })
        self._emitted += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        details = {"seen": self._seen, "passed": self._passed, "emitted": self._emitted,
                   "missing": self._missing, "stale": self._stale,
                   "cycle_mismatch": self._cycle_mismatch,
                   "news_windows": sum(len(bucket) for bucket
                                       in self._news_windows.values()),
                   "last_blocked_by": list(self._last_blocked_by)}
        if self._seen == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NO_INPUT, details=details)
        if self._last_blocked_by:
            blocked = " ".join(
                "%s=%s" % (bucket, ",".join(items))
                for bucket, items in (self._last_buckets or {}).items() if items) or \
                ",".join(self._last_blocked_by[:5])
        else:
            blocked = "-"
        return HealthStatus(state=HealthState.HEALTHY,
                            message="seen=%d passed=%d emitted=%d last_blocked=%s" % (
                                self._seen, self._passed, self._emitted, blocked), details=details)
