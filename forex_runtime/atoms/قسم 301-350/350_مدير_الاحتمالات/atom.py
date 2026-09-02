from __future__ import annotations

import time
from collections import deque
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.probability_contract import STATE_NOT_READY, STATE_READY, clip
from shared.section_contract import section_atom
from shared.tick_contract import VALIDATED_TICK_EVENT, as_validated_tick

ATOM_VERSION = "3.3.0"
# v3.2.0 (2026-08-25): panel emission is PACED, not per unit event. Measured
# py-spy under the freed feed: building and publishing the full units panel
# on EVERY unit delivery drowned the loop (350/400 SYS_SECOND handlers blew
# the 30s isolation limit every cycle). The panel is a display surface --
# it emits at most once per second per scope; the SYS_SECOND pulse flushes
# whatever went dirty in between, so the newest state always lands.
_PANEL_MIN_INTERVAL_S = 1.0
# v3.3.0 (2026-08-25, second measurement): the CARDS themselves are paced.
# py-spy after the panel fix: _fast_copy was 46% of the main thread, fed by
# ~12 full section cards/second (each embedding every unit's whole payload,
# copied per subscriber). Downstream is the ROOM (451): last value per
# source wins by owner design, so intermediate completed cycles between
# paces carry no decision value -- the NEWEST card is kept, intermediate
# ones are counted (superseded) and the SYS_SECOND pulse flushes.
_CARD_MIN_INTERVAL_S = 0.25
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_TIME = "SYS_SECOND"
EVENT_OUT = "probability.cycle.collected"
EVENT_LIVE = "probability.section.live"
# Section 350 closure: the units panel -- every probability unit declares itself.
EVENT_PANEL = "probability.units.state"
UNIT_EVENTS = (
    "probability.trend.state",
    "probability.reversal.state",
    "probability.breakout.state",
    "probability.pullback.state",
    "probability.momentum.state",
    "probability.range.state",
    "probability.hurst.state",
    "probability.merged.state",
    "probability.confidence.state",
)
EXPECTED_ORDER = (
    "trend_model",
    "reversal_model",
    "breakout_model",
    "pullback_model",
    "momentum_model",
    "range_model",
    "hurst",
    "models_merged",
    "confidence_aggregator",
)
EXPECTED = frozenset(EXPECTED_ORDER)
RECENT_CAP = 256


def num(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result else 0.0


@section_atom("350", "350")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._cycles = {}
        self._recent = deque(maxlen=RECENT_CAP)
        self._timeout = 1.0
        self._now = 0.0
        self._section_weight = 100.0 / 6.0
        self._required_depth = 60.0
        self._confidence_threshold = 60.0
        self._opened = 0
        self._forwarded = 0
        self._invalid = 0
        self._duplicates = 0
        self._late = 0
        # Section 350 closure: per-unit self-declared registry.
        self._unit_latest = {}
        self._unit_deliveries = {}
        self._panel_emitted = 0
        self._panel_last = {}
        self._panel_dirty = set()
        self._card_last = {}
        self._card_pending = {}
        self._cards_superseded = 0

    async def initialize(self, context):
        self._context = context
        cfg = context.config
        self._timeout = float(cfg.get("timeout_seconds", 1.0))
        self._section_weight = clip(cfg.get("section_weight", 100.0 / 6.0))
        self._required_depth = clip(cfg.get("required_depth", 60.0))
        self._confidence_threshold = clip(cfg.get("confidence_threshold", 60.0))
        # Section 350 closure (owner vision, atoms-first 2026-08-23): cycles
        # open ONLY from unit deliveries -- the unit-driven path below already
        # exists; the per-tick empty cycles are retired (same cure as 200).
        context.subscribe(EVENT_TIME, self._on_time)
        for event, unit_id in zip(UNIT_EVENTS, EXPECTED_ORDER):
            context.subscribe(event, self._handler(unit_id))

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    def _handler(self, expected_id):
        async def handler(payload):
            await self._on_unit(payload, expected_id)

        return handler

    async def _on_unit(self, payload: dict[str, Any], expected_id: str):
        if not self._running or not isinstance(payload, dict):
            return
        cid = str(payload.get("cycle_id") or "")
        unit = str(payload.get("model_id") or payload.get("id") or "")
        cycle = self._cycles.get(cid)
        if cycle is None:
            if any(old == cid and unit in units for old, units in self._recent):
                return
            if (
                cid
                and payload.get("account_id")
                and payload.get("broker")
                and payload.get("symbol")
            ):
                cycle = {
                    "account_id": payload.get("account_id"),
                    "broker": payload.get("broker"),
                    "symbol": payload.get("symbol"),
                    "period_start": payload.get("period_start"),
                    "source_timestamp": payload.get("source_timestamp"),
                    "timestamp": payload.get("timestamp"),
                    "open_time": self._now,
                    "results": {},
                }
                self._cycles[cid] = cycle
                self._opened += 1
            else:
                self._late += 1
                return
        if unit != expected_id or unit not in EXPECTED:
            self._invalid += 1
            return
        if (
            str(payload.get("account_id") or "") != str(cycle["account_id"])
            or str(payload.get("broker") or "") != str(cycle["broker"])
            or str(payload.get("symbol") or "") != str(cycle["symbol"])
        ):
            self._invalid += 1
            return
        if unit in cycle["results"]:
            self._duplicates += 1
            return
        cycle["results"][unit] = dict(payload)
        scope = (str(payload.get("account_id") or ""), str(payload.get("broker") or ""),
                 str(payload.get("symbol") or ""), str(payload.get("timeframe") or ""))
        self._unit_latest[(scope, unit)] = {
            "direction": num(payload.get("direction")),
            "confidence": num(payload.get("confidence")),
            "probability": payload.get("probability"),
            "weight": payload.get("weight"),
            "ready": payload.get("ready") is True,
            "state": str(payload.get("state") or payload.get("analysis_state") or ""),
            "timeframe": payload.get("timeframe"),
            "period_start": payload.get("period_start"),
            "delivered_wall": self._now}
        key = (scope, unit)
        self._unit_deliveries[key] = self._unit_deliveries.get(key, 0) + 1
        await self._emit_panel(scope)
        if len(cycle["results"]) == len(EXPECTED):
            await self._forward(cid, "complete")

    async def _on_time(self, payload):
        if not self._running or not isinstance(payload, dict):
            return
        self._now = num(payload.get("official_time"))
        expired = [
            cid
            for cid, row in self._cycles.items()
            if row["open_time"] > 0 and self._now - row["open_time"] > self._timeout
        ]
        for cid in expired:
            await self._forward(cid, "timed_out")
        # v3.2.0: flush paced panels -- the pulse IS the pace, so it forces.
        for scope in list(self._panel_dirty):
            await self._emit_panel(scope, force=True)
        # v3.3.0: flush paced cards -- the newest pending card per scope.
        for scope3 in list(self._card_pending):
            card = self._card_pending.pop(scope3)
            self._card_last[scope3] = time.monotonic()
            await self._publish_card(card)

    async def _forward(self, cid, reason):
        cycle = self._cycles.pop(cid, None)
        if cycle is None or self._context is None:
            return
        results = cycle["results"]
        self._recent.append((cid, frozenset(results)))
        merged = results.get("models_merged", {})
        confidence_card = results.get("confidence_aggregator", {})
        complete = len(results) == len(EXPECTED)
        direction = num(merged.get("direction"))
        strength = num(merged.get("strength"))
        confidence = num(confidence_card.get("confidence", merged.get("confidence")))
        depth = (
            min(
                num(merged.get("current_depth")),
                num(confidence_card.get("current_depth")),
            )
            if confidence_card
            else num(merged.get("current_depth"))
        )
        ready = (
            complete
            and merged.get("ready") is True
            and confidence_card.get("ready") is True
            and depth >= self._required_depth
            and confidence >= self._confidence_threshold
        )
        state = STATE_READY if ready else STATE_NOT_READY
        missing = [item for item in EXPECTED_ORDER if item not in results]
        card = {
            "account_id": cycle["account_id"],
            "broker": cycle["broker"],
            "symbol": cycle["symbol"],
            "timeframe": "tick",
            "period_start": cycle["period_start"],
            "cycle_id": cid,
            "source_timestamp": cycle["source_timestamp"],
            "timestamp": cycle["timestamp"],
            "id": "probability_section",
            "section_id": "350",
            "analysis_mode": "live_tick",
            "contract_version": 2,
            "status": "ok",
            "results": results,
            "expected": len(EXPECTED),
            "present": len(results),
            "missing": missing,
            "complete": complete,
            "cycle_status": "complete" if complete else "incomplete",
            "close_reason": reason,
            "signal": (
                "buy" if direction > 0 else "sell" if direction < 0 else "neutral"
            ),
            "direction": round(direction, 6),
            "score": round(direction, 6),
            "strength": round(strength, 6),
            "confidence": round(confidence, 6),
            "current_depth": round(depth, 6),
            "required_depth": round(self._required_depth, 6),
            "confidence_threshold": round(self._confidence_threshold, 6),
            "threshold": round(self._confidence_threshold, 6),
            "weight": round(self._section_weight, 6),
            "weight_applied": round(self._section_weight if ready else 0.0, 6),
            "ratio": round(self._section_weight, 6),
            "readiness_pct": (round(min(100.0, depth / self._required_depth * 100.0), 1)
                              if self._required_depth > 0 else None),
            "ready": ready,
            "analysis_state": state,
            "state": state,
            "quality": "good" if ready else "low",
            "warnings": [] if ready else ["PROBABILITY_SECTION_NOT_READY"],
        }
        # v3.3.0: card stream paced per scope -- newest wins, intermediate
        # counted; the SYS_SECOND pulse flushes what is pending.
        scope3 = (str(cycle["account_id"]), str(cycle["broker"]),
                  str(cycle["symbol"]))
        mono = time.monotonic()
        if mono - self._card_last.get(scope3, 0.0) < _CARD_MIN_INTERVAL_S:
            if scope3 in self._card_pending:
                self._cards_superseded += 1
            self._card_pending[scope3] = card
            return
        self._card_last[scope3] = mono
        await self._publish_card(card)

    async def _publish_card(self, card):
        await self._context.publish(EVENT_OUT, dict(card))
        await self._context.publish(EVENT_LIVE, dict(card))
        self._forwarded += 1


    async def _emit_panel(self, scope, force=False):
        # Section 350 closure: one event, one row per expected unit -- each
        # unit declares ITSELF (what it said, its rhythm, deliveries, age).
        if self._context is None:
            return
        # v3.2.0: paced -- a burst marks the scope dirty; SYS_SECOND flushes
        # with force=True (the pulse is already the pace; re-checking the
        # window inside the flush would starve a dirty panel another cycle).
        mono = time.monotonic()
        if not force and mono - self._panel_last.get(scope, 0.0) < _PANEL_MIN_INTERVAL_S:
            self._panel_dirty.add(scope)
            return
        self._panel_last[scope] = mono
        self._panel_dirty.discard(scope)
        account, broker, symbol, timeframe = scope
        rows = []
        for unit_id in EXPECTED_ORDER:
            declared = self._unit_latest.get((scope, unit_id))
            if declared is None:
                rows.append({"id": unit_id, "present": False})
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
            rows.append({"id": unit_id, "present": True,
                         "direction": declared.get("direction"),
                         "confidence": declared.get("confidence"),
                         "probability": declared.get("probability"),
                         "ready": declared.get("ready"),
                         "state": declared.get("state"),
                         "timeframe": declared.get("timeframe"),
                         "period_start": declared.get("period_start"),
                         "deliveries": self._unit_deliveries.get((scope, unit_id), 0),
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

    async def snapshot(self):
        return {
            "cycles": self._cycles,
            "recent": [[cid, list(units)] for cid, units in self._recent],
            "opened": self._opened,
            "forwarded": self._forwarded,
            "invalid": self._invalid,
            "duplicates": self._duplicates,
            "late": self._late,
        }

    async def restore(self, state):
        if not isinstance(state, dict):
            return
        self._cycles = (
            state.get("cycles", {}) if isinstance(state.get("cycles", {}), dict) else {}
        )
        self._recent = deque(
            (
                (str(row[0]), frozenset(row[1]))
                for row in state.get("recent", [])
                if isinstance(row, list) and len(row) == 2
            ),
            maxlen=RECENT_CAP,
        )
        self._opened = int(state.get("opened", 0))
        self._forwarded = int(state.get("forwarded", 0))
        self._invalid = int(state.get("invalid", 0))
        self._duplicates = int(state.get("duplicates", 0))
        self._late = int(state.get("late", 0))

    async def health_check(self):
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        return HealthStatus(
            state=HealthState.HEALTHY if self._opened else HealthState.DEGRADED,
            message="opened=%d forwarded=%d superseded=%d open=%d late=%d units=%d panel=%d" % (
                self._opened, self._forwarded, self._cards_superseded,
                len(self._cycles), self._late,
                len(self._unit_latest), self._panel_emitted),
            details={
                "opened": self._opened,
                "forwarded": self._forwarded,
                "open": len(self._cycles),
                "invalid": self._invalid,
                "duplicates": self._duplicates,
                "late": self._late,
                "units_tracked": len(self._unit_latest),
                "panel_emitted": self._panel_emitted,
            },
        )
