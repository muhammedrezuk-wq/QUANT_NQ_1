from __future__ import annotations
import time
from collections import deque
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom
from shared.strategy_contract import ALL_IDS, STATE_NOT_READY, STATE_READY, clip
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "3.5.0"
# v3.5.0 (2026-08-25): the pace gate moved BEFORE the card build (a paced-
# out card was still being BUILT then stashed -- the build itself was the
# CPU), the stash holds the raw cycle and the card is built only when it
# actually publishes, and the expiry sweep drains a bounded bite per pulse.
_EXPIRE_MAX_PER_PULSE = 500
# v3.3.0 (2026-08-25): panel emission PACED (once per second per scope, the
# SYS_SECOND pulse flushes dirty scopes) -- building the full panel on every
# unit delivery drowned the loop under the freed feed (measured: 30s handler
# isolation tripped every cycle on 350/400).
_PANEL_MIN_INTERVAL_S = 1.0
# v3.4.0 (2026-08-25, second measurement): the CARDS are paced too --
# _fast_copy was 46% of the main thread, fed by ~19 full section cards/s
# from 350+400. Downstream is the ROOM (451, last value per source by owner
# design): the NEWEST card per scope is kept, intermediates are counted
# (superseded), the SYS_SECOND pulse flushes.
_CARD_MIN_INTERVAL_S = 0.25
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_TIME = "SYS_SECOND"
EVENT_OUT = "strategy.cycle.collected"
EVENT_LIVE = "strategy.section.live"
# Section 400 closure: the units panel -- every strategy declares itself.
EVENT_PANEL = "strategy.units.state"
AGGREGATE_EVENT = "strategy.aggregate.state"
AGGREGATE_ID = "strategy_aggregate"
COMPONENT_EVENTS = (
    "strategy.trend.state",
    "strategy.reversal.state",
    "strategy.breakout.state",
    "strategy.pullback.state",
    "strategy.momentum.state",
    "strategy.range.state",
    "strategy.liquidity.state",
    "strategy.entry_quality.state",
    "strategy.invalidation_quality.state",
    "strategy.news.state",
    "strategy.session.state",
    AGGREGATE_EVENT,
)
EXPECTED = frozenset((*ALL_IDS, AGGREGATE_ID))


def num(v):
    try:
        r = float(v)
    except (TypeError, ValueError):
        return 0.0
    return r if r == r else 0.0


@section_atom("400", "400")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._cycles = {}
        self._recent = deque(maxlen=256)
        self._section_weight = 100 / 6
        self._required_depth = 60.0
        self._confidence_threshold = 60.0
        self._timeout_s = 5.0
        self._now = 0.0
        self._timed_out = 0
        # Section 400 closure: per-unit self-declared registry.
        self._unit_latest = {}
        self._unit_deliveries = {}
        self._panel_emitted = 0
        self._panel_last = {}
        self._panel_dirty = set()
        self._card_last = {}
        self._card_pending = {}
        self._cards_superseded = 0
        self._opened = self._forwarded = self._invalid = self._duplicates = 0

    async def initialize(self, c):
        self._context = c
        self._section_weight = clip(c.config.get("section_weight", 100 / 6))
        self._required_depth = clip(c.config.get("required_depth", 60))
        self._confidence_threshold = clip(c.config.get("confidence_threshold", 60))
        # Section 400 closure (owner vision, atoms-first 2026-08-23): cycles
        # open ONLY from component deliveries (the path already existed) --
        # the per-tick opener is retired, same cure as 200/250/300/350.
        c.subscribe(EVENT_TIME, self._on_time)
        for event in COMPONENT_EVENTS:
            c.subscribe(event, self._on_component)

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    async def _on_time(self, p: dict[str, Any]):
        # Measures open-cycle timeout (staleness/timeouts) — SYS_SECOND.
        if not self._running or not isinstance(p, dict):
            return
        import clock
        self._now = clock.now()
        expired = [cid for cid, cyc in self._cycles.items()
                   if cyc.get("open_time", 0) > 0 and self._now - cyc["open_time"] > self._timeout_s]
        # v3.5.0: a restored/accumulated backlog is drained in bounded bites —
        # measured: thousands of expired cycles in one pulse blew the 30s
        # handler isolation every cycle after boot. Never dropped: the rest
        # goes on the next pulse.
        for cid in expired[:_EXPIRE_MAX_PER_PULSE]:
            # Section 400 closure: a timed-out cycle is FORWARDED with what it
            # has (declared incomplete) -- never silently dropped.
            self._timed_out += 1
            await self._forward(cid)
        # v3.3.0: flush paced panels -- the pulse IS the pace, so it forces.
        for scope in list(self._panel_dirty):
            await self._emit_panel(scope, force=True)
        # v3.5.0: flush paced cards -- the newest pending CYCLE per scope,
        # built only now (build cost paid once per pace window).
        for scope3 in list(self._card_pending):
            cid, cycle = self._card_pending.pop(scope3)
            self._card_last[scope3] = time.monotonic()
            await self._publish_card(self._build_card(cid, cycle))

    async def _on_component(self, p: dict[str, Any]):
        if not self._running or self._context is None or not isinstance(p, dict):
            return
        cid = str(p.get("cycle_id") or "")
        sid = str(p.get("strategy_id") or p.get("id") or "")
        if not cid or sid not in EXPECTED:
            self._invalid += 1
            return
        cycle = self._cycles.get(cid)
        if cycle is None:
            cycle = {"identity": dict(p), "results": {}, "open_time": self._now}
            self._cycles[cid] = cycle
            self._opened += 1
        if sid in cycle["results"]:
            self._duplicates += 1
            return
        cycle["results"][sid] = dict(p)
        scope = (str(p.get("account_id") or ""), str(p.get("broker") or ""),
                 str(p.get("symbol") or ""), str(p.get("timeframe") or ""))
        self._unit_latest[(scope, sid)] = {
            "direction": p.get("direction"),
            "strength": p.get("strength"),
            "confidence": p.get("confidence"),
            "weight": p.get("weight"),
            "ready": p.get("ready") is True,
            "state": str(p.get("state") or p.get("analysis_state") or ""),
            "timeframe": p.get("timeframe"),
            "period_start": p.get("period_start"),
            "delivered_wall": self._now}
        key = (scope, sid)
        self._unit_deliveries[key] = self._unit_deliveries.get(key, 0) + 1
        await self._emit_panel(scope)
        if len(cycle["results"]) == len(EXPECTED):
            await self._forward(cid)

    async def _forward(self, cid):
        cycle = self._cycles.pop(cid, None)
        if cycle is None or self._context is None:
            return
        self._recent.append(cid)
        identity = cycle.get("identity") or {}
        # v3.5.0: pace FIRST -- a paced-out cycle is stashed RAW and its card
        # is built only if it is still the newest when the pulse flushes.
        scope3 = (str(identity.get("account_id") or ""),
                  str(identity.get("broker") or ""),
                  str(identity.get("symbol") or ""))
        mono = time.monotonic()
        if mono - self._card_last.get(scope3, 0.0) < _CARD_MIN_INTERVAL_S:
            if scope3 in self._card_pending:
                self._cards_superseded += 1
            self._card_pending[scope3] = (cid, cycle)
            return
        self._card_last[scope3] = mono
        await self._publish_card(self._build_card(cid, cycle))

    def _build_card(self, cid, cycle):
        identity = cycle["identity"]
        results = cycle["results"]
        aggregate = results.get(AGGREGATE_ID, {})
        direction = num(aggregate.get("direction"))
        strength = num(aggregate.get("strength"))
        raw_confidence = aggregate.get("confidence")
        confidence_defined = (
            isinstance(raw_confidence, (int, float))
            and not isinstance(raw_confidence, bool)
        )
        confidence = num(raw_confidence)
        depth = num(aggregate.get("current_depth"))
        readiness = (
            round(min(100.0, depth / self._required_depth * 100.0), 1)
            if self._required_depth > 0
            else None
        )
        ready = (
            aggregate.get("ready") is True
            and depth >= self._required_depth
            and confidence_defined
            and confidence >= self._confidence_threshold
        )
        state = STATE_READY if ready else STATE_NOT_READY
        card = {
            "account_id": identity.get("account_id"),
            "broker": identity.get("broker"),
            "symbol": identity.get("symbol"),
            "timeframe": "tick",
            "period_start": identity.get("period_start"),
            "cycle_id": cid,
            "source_timestamp": identity.get(
                "source_timestamp", identity.get("timestamp")
            ),
            "timestamp": identity.get("timestamp"),
            "section_id": "400",
            "id": "strategy_section",
            "analysis_mode": "live_tick",
            "contract_version": 2,
            "status": "ok",
            "results": results,
            "expected": len(EXPECTED),
            "present": len(results),
            "missing": [x for x in EXPECTED if x not in results],
            "complete": len(results) == len(EXPECTED),
            "cycle_status": (
                "complete" if len(results) == len(EXPECTED) else "incomplete"
            ),
            "signal": (
                "positive_strategic_lean"
                if direction > 0
                else (
                    "negative_strategic_lean"
                    if direction < 0
                    else "balanced_strategic_context"
                )
            ),
            "direction": round(direction, 6),
            "score": round(direction, 6),
            "strength": round(strength, 6),
            "confidence": round(confidence, 6) if confidence_defined else None,
            "confidence_defined": confidence_defined,
            "readiness_pct": readiness,
            "current_depth": round(depth, 6),
            "required_depth": round(self._required_depth, 6),
            "confidence_threshold": round(self._confidence_threshold, 6),
            "threshold": round(self._confidence_threshold, 6),
            "weight": round(self._section_weight, 6),
            "weight_applied": round(self._section_weight if ready else 0.0, 6),
            "ratio": round(self._section_weight, 6),
            "ready": ready,
            "analysis_state": state,
            "state": state,
            "active_weight": aggregate.get("active_weight", 0.0),
            "available_weight": aggregate.get("available_weight", 0.0),
            "missing_weight": aggregate.get("missing_weight", 0.0),
            "context_factor": aggregate.get("context_factor", 0.0),
            "quality": "good" if ready else "low",
            "warnings": (
                [] if ready else
                (["CONFIDENCE_UNDEFINED"] if not confidence_defined
                 else ["STRATEGY_SECTION_NOT_READY"])
            ),
        }
        return card

    async def _publish_card(self, card):
        await self._context.publish(EVENT_OUT, dict(card))
        await self._context.publish(EVENT_LIVE, dict(card))
        self._forwarded += 1

    async def snapshot(self):
        return {
            "cycles": self._cycles,
            "recent": list(self._recent),
            "opened": self._opened,
            "forwarded": self._forwarded,
            "invalid": self._invalid,
            "duplicates": self._duplicates,
        }

    async def restore(self, x):
        if not isinstance(x, dict):
            return
        self._cycles = (
            x.get("cycles", {}) if isinstance(x.get("cycles", {}), dict) else {}
        )
        self._recent = deque((str(v) for v in x.get("recent", [])), maxlen=256)
        self._opened = int(x.get("opened", 0))
        self._forwarded = int(x.get("forwarded", 0))
        self._invalid = int(x.get("invalid", 0))
        self._duplicates = int(x.get("duplicates", 0))


    async def _emit_panel(self, scope, force=False):
        # Section 400 closure: one event, one row per expected strategy (and
        # the aggregate) -- each declares ITSELF. Confidence may be None
        # (undefined) and passes as None, declared.
        if self._context is None:
            return
        # v3.3.0: paced -- a burst marks the scope dirty; SYS_SECOND flushes
        # with force=True (the pulse is already the pace).
        mono = time.monotonic()
        if not force and mono - self._panel_last.get(scope, 0.0) < _PANEL_MIN_INTERVAL_S:
            self._panel_dirty.add(scope)
            return
        self._panel_last[scope] = mono
        self._panel_dirty.discard(scope)
        account, broker, symbol, timeframe = scope
        rows = []
        for sid in (*ALL_IDS, AGGREGATE_ID):
            declared = self._unit_latest.get((scope, sid))
            if declared is None:
                rows.append({"id": sid, "present": False})
                continue
            next_expected = None
            text = str(declared.get("timeframe") or "").strip().lower()
            units_map = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
            if len(text) >= 2 and text[-1] in units_map:
                try:
                    start = float(declared.get("period_start"))
                    next_expected = start + float(text[:-1]) * units_map[text[-1]]
                except (TypeError, ValueError):
                    next_expected = None
            rows.append({"id": sid, "present": True,
                         "direction": declared.get("direction"),
                         "strength": declared.get("strength"),
                         "confidence": declared.get("confidence"),
                         "confidence_defined": declared.get("confidence") is not None,
                         "weight": declared.get("weight"),
                         "ready": declared.get("ready"),
                         "state": declared.get("state"),
                         "timeframe": declared.get("timeframe"),
                         "period_start": declared.get("period_start"),
                         "deliveries": self._unit_deliveries.get((scope, sid), 0),
                         "age_s": (round(self._now - declared["delivered_wall"], 1)
                                   if declared.get("delivered_wall") is not None else None),
                         "next_expected_at": next_expected})
        present = sum(1 for row in rows if row.get("present"))
        await self._context.publish(EVENT_PANEL, {
            "account_id": account or None, "broker": broker or None,
            "symbol": symbol, "timeframe": timeframe or None,
            "units": rows, "expected": len(EXPECTED), "present": present,
            "missing": [row["id"] for row in rows if not row.get("present")]})
        self._panel_emitted += 1

    async def health_check(self):
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY if self._opened else HealthState.DEGRADED,
            message="opened=%d forwarded=%d superseded=%d open=%d timed_out=%d units=%d panel=%d" % (
                self._opened, self._forwarded, self._cards_superseded,
                len(self._cycles), self._timed_out,
                len(self._unit_latest), self._panel_emitted),
            details={
                "opened": self._opened,
                "forwarded": self._forwarded,
                "open": len(self._cycles),
                "invalid": self._invalid,
                "duplicates": self._duplicates,
                "timed_out": self._timed_out,
                "units_tracked": len(self._unit_latest),
                "panel_emitted": self._panel_emitted,
            },
        )
