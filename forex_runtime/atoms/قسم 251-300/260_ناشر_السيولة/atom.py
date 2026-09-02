from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.section_contract import section_atom

ATOM_VERSION = "1.2.0"

EVENT_IN = "liquidity.cycle.validated"
EVENT_OUT = "market.liquidity.updated"

ID_LIQUIDITY = "liquidity"

UID_POOL = "pool"
UID_BUY = "buyside"
UID_SELL = "sellside"
UID_SWEEP = "sweep"
UID_FVG = "fvg"
# Order-flow family (delta/cvd/absorption), independent of the structure
# family above.
UID_DELTA = "delta"
UID_CVD = "cvd"
UID_ABSORPTION = "absorption"

SIGNAL_SWEEP = "sweep"
SIGNAL_ABSORBED = "absorbed"
DEFAULT_NONE = "none"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"

QUALITY_GOOD = "good"
QUALITY_LOW = "low"

REASON_NOT_STARTED = "NOT_STARTED"
REASON_NOTHING = "NOTHING_PUBLISHED_YET"

REQUIRED_DEPTH = 100.0

PRESSURE_EPSILON = 1e-9
ABSORPTION_DAMP_FACTOR = 0.5
INTEGRITY_WEIGHT = 0.6
COVERAGE_WEIGHT = 0.4
QUALITY_AGREE_BONUS = 10.0
QUALITY_DISAGREE_PENALTY = -15.0


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: float) -> float:
    return max(0.0, min(100.0, value))


_SIDE_WORDS: dict[str, float] = {
    "buy_side": -1.0, "buyside": -1.0, "sell_side": 1.0, "sellside": 1.0,
    "bullish": 1.0, "bearish": -1.0, "up": 1.0, "down": -1.0,
    "buy": 1.0, "sell": -1.0,
}


def _side_of(results: dict[str, Any], uid: str) -> float | None:
    state = results.get(uid)
    if not isinstance(state, dict) or state.get("status") != STATUS_OK:
        return None
    meta = state.get("metadata") or {}
    for word in (meta.get("direction"), state.get("signal"), uid):
        key = str(word or "").strip().lower()
        if key in _SIDE_WORDS:
            return _SIDE_WORDS[key]
    return None


def _signal(results: dict[str, Any], uid: str, default: str) -> Any:
    state = results.get(uid)
    return state.get("signal", default) if isinstance(state, dict) else default


def _meta(results: dict[str, Any], uid: str, field: str) -> Any:
    state = results.get(uid)
    if isinstance(state, dict):
        meta = state.get("metadata") or {}
        return meta.get(field)
    return None


def _ok_state(results: dict[str, Any], uid: str) -> dict[str, Any] | None:
    state = results.get(uid)
    return state if isinstance(state, dict) and state.get("status") == STATUS_OK else None


def _flow_pressure(results: dict[str, Any]) -> float | None:
    """Order-flow pressure from delta+cvd (confidence-weighted), damped by absorption."""
    weighted: list[tuple[float, float]] = []
    delta_state = _ok_state(results, UID_DELTA)
    if delta_state is not None:
        ratio = _num((delta_state.get("metadata") or {}).get("ratio"))
        conf = _num(delta_state.get("confidence"))
        if ratio is not None and conf is not None and conf > 0:
            weighted.append((max(-1.0, min(1.0, ratio)), conf))
    cvd_state = _ok_state(results, UID_CVD)
    if cvd_state is not None:
        cvd_delta = _num((cvd_state.get("metadata") or {}).get("delta"))
        conf = _num(cvd_state.get("confidence"))
        if cvd_delta is not None and conf is not None and conf > 0:
            sign = 1.0 if cvd_delta > 0 else (-1.0 if cvd_delta < 0 else 0.0)
            weighted.append((sign, conf))
    if not weighted:
        return None
    total_conf = sum(conf for _, conf in weighted)
    raw = sum(sign * conf for sign, conf in weighted) / total_conf
    absorb_state = _ok_state(results, UID_ABSORPTION)
    if absorb_state is not None and absorb_state.get("signal") == SIGNAL_ABSORBED:
        damp = _num(absorb_state.get("confidence"))
        damp = 1.0 if damp is None else max(0.0, min(1.0, damp))
        raw *= (1.0 - ABSORPTION_DAMP_FACTOR * damp)
    return round(max(-100.0, min(100.0, raw * 100.0)), 4)


def _quality_with_flow(base: float | None, direction_avg: float | None,
                       pressure: float | None) -> float | None:
    """Liquidity quality: base (integrity+coverage) adjusted by agreement
    between structure direction and order-flow pressure."""
    if base is None:
        return None
    if pressure is None or direction_avg is None or abs(pressure) < PRESSURE_EPSILON:
        return round(_clip(base), 4)
    agree = (direction_avg >= 0) == (pressure >= 0)
    adjustment = QUALITY_AGREE_BONUS if agree else QUALITY_DISAGREE_PENALTY
    return round(_clip(base + adjustment), 4)


@section_atom("250", "260")
class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._seen = 0
        self._published = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        context.subscribe(EVENT_IN, self._on_validated)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    async def _on_validated(self, payload: dict[str, Any]) -> None:
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        symbol = payload.get("symbol")
        if not symbol:
            return
        symbol = str(symbol)
        self._seen += 1
        cycle_id = str(payload.get("cycle_id", ""))
        timeframe = str(payload.get("timeframe", ""))
        results = payload.get("results") or {}
        sweep_sig = _signal(results, UID_SWEEP, DEFAULT_NONE)
        fvg_sig = _signal(results, UID_FVG, DEFAULT_NONE)
        if sweep_sig == SIGNAL_SWEEP:
            headline = SIGNAL_SWEEP
        elif fvg_sig != DEFAULT_NONE:
            headline = fvg_sig
        else:
            headline = DEFAULT_NONE
        valid = [s for s in results.values()
                 if isinstance(s, dict) and s.get("status") == STATUS_OK]
        expected = _count(payload.get("expected"))
        current_depth = (100.0 * len(valid) / expected) if expected > 0 else 0.0
        complete = expected > 0 and len(valid) >= expected
        status = STATUS_OK if complete else STATUS_INSUFFICIENT
        quality = QUALITY_GOOD if complete else QUALITY_LOW
        confidences = [_num(state.get("confidence")) for state in valid]
        confidences = [value for value in confidences if value is not None]
        sides: list[float] = []
        for uid in (UID_SWEEP, UID_FVG, UID_BUY, UID_SELL):
            side = _side_of(results, uid)
            if side is not None:
                sides.append(side)
        direction_avg = (sum(sides) / len(sides)) if sides else None
        direction = round(direction_avg * 100.0, 4) if direction_avg is not None else None
        intensities = [value for value in confidences if 0.0 <= value <= 1.0]
        abnormality = (sum(intensities) / len(intensities) * 100.0
                       if intensities else None)
        integrity = (abs(sum(sides)) / len(sides) * 100.0) if sides else None
        strength = (round(_clip(abnormality * integrity / 100.0), 4)
                    if abnormality is not None and integrity is not None
                    else None)
        coverage = (100.0 * len(valid) / expected) if expected > 0 else 0.0
        section_confidence = (round(_clip(INTEGRITY_WEIGHT * integrity
                                          + COVERAGE_WEIGHT * coverage), 4)
                              if integrity is not None else None)
        # Pressure and quality are independent signals, not direction/confidence copies.
        # Without order-flow evidence (delta/cvd), pressure degrades to the structural
        # direction reading rather than going unknown -- still real, just less independent.
        pressure = _flow_pressure(results)
        if pressure is None:
            pressure = direction
        liquidity_quality = _quality_with_flow(section_confidence, direction_avg, pressure)
        liquidity = {
            "pool": _signal(results, UID_POOL, DEFAULT_NONE),
            "buyside_level": _meta(results, UID_BUY, "price"),
            "sellside_level": _meta(results, UID_SELL, "price"),
            "sweep": {"signal": sweep_sig, "direction": _meta(results, UID_SWEEP, "direction"),
                      "price": _meta(results, UID_SWEEP, "price")},
            "fvg": {"signal": fvg_sig, "gap_top": _meta(results, UID_FVG, "gap_top"),
                    "gap_bottom": _meta(results, UID_FVG, "gap_bottom")}}
        meta = {"timeframe": timeframe, "present": payload.get("present", 0),
                "expected": payload.get("expected", 0)}
        await self._context.publish(EVENT_OUT, {
            "symbol": symbol, "id": ID_LIQUIDITY, "cycle_id": cycle_id,
            "timeframe": timeframe,
            "account_id": payload.get("account_id"),
            "broker": payload.get("broker"),
            "status": status, "signal": headline,
            "directional": direction is not None,
            "direction": direction,
            "strength": strength,
            "liquidity_pressure": pressure,
            "liquidity_quality": liquidity_quality,
            "confidence": section_confidence,
            "current_depth": round(current_depth, 4),
            "required_depth": REQUIRED_DEPTH,
            "complete": complete,
            "quality": quality, "warnings": [],
            "liquidity": liquidity, "metadata": meta})
        self._published += 1

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        if self._published == 0:
            return HealthStatus(state=HealthState.DEGRADED, message=REASON_NOTHING)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="seen=%d published=%d" % (self._seen, self._published),
            details={"seen": self._seen, "published": self._published})
