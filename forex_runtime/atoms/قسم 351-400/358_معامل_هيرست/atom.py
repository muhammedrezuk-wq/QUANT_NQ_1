from __future__ import annotations

import math
from typing import Any
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.probability_contract import TickModelRuntime, clip
from shared.section_contract import section_atom
from shared.tick_contract import VALIDATED_TICK_EVENT

ATOM_VERSION = "2.0.0"
EVENT_TICK = VALIDATED_TICK_EVENT
EVENT_OUT = "probability.hurst.state"
MODEL_ID = "hurst"
MIN_HURST_SAMPLES = 20
MIN_WINDOW = 32
MIDPOINT = 0.5
PERCENT = 100.0
HURST_STRENGTH_SCALE = 200.0
NEUTRAL_PROBABILITY = 0.5
HURST_SCALES = (8, 16, 32, 64)


def _hurst(values: list[float]) -> float | None:
    if len(values) < MIN_HURST_SAMPLES:
        return None
    points = []
    for size in HURST_SCALES:
        if size > len(values):
            continue
        chunks = [values[i : i + size] for i in range(0, len(values) - size + 1, size)]
        ratios = []
        for chunk in chunks:
            mean = sum(chunk) / len(chunk)
            running = 0.0
            path = []
            for value in chunk:
                running += value - mean
                path.append(running)
            span = max(path) - min(path)
            variance = sum((x - mean) ** 2 for x in chunk) / len(chunk)
            std = math.sqrt(variance)
            if span > 0 and std > 0:
                ratios.append(span / std)
        if ratios:
            points.append((math.log(size), math.log(sum(ratios) / len(ratios))))
    if len(points) < 2:
        return None
    xbar = sum(x for x, _ in points) / len(points)
    ybar = sum(y for _, y in points) / len(points)
    den = sum((x - xbar) ** 2 for x, _ in points)
    return sum((x - xbar) * (y - ybar) for x, y in points) / den if den else None


@section_atom("350", "358")
class Atom(AtomBase):
    def __init__(self):
        self._context = None
        self._running = False
        self._runtime = TickModelRuntime(MODEL_ID)
        self._window = 100
        self._band = 0.05
        self._ticks = 0
        self._emitted = 0

    async def initialize(self, context):
        self._context = context
        self._runtime.configure(context.config)
        self._window = max(MIN_WINDOW, int(context.config.get("window", 100)))
        self._band = float(context.config.get("band", 0.05))
        context.subscribe(EVENT_TICK, self._on_tick)

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def shutdown(self):
        await self.stop()

    async def _on_tick(self, payload: dict[str, Any]):
        if not self._running or self._context is None or not isinstance(payload, dict):
            return
        item = self._runtime.ingest(payload)
        if item is None:
            return
        tick, state = item
        returns = list(state.returns)[-self._window :]
        self._ticks += 1
        value = _hurst(returns)
        if value is None:
            card = self._runtime.card(
                tick,
                state,
                direction=0,
                strength=0,
                confidence=0,
                probability=NEUTRAL_PROBABILITY,
                signal="random_walk",
                status="insufficient_data",
                directional=False,
                evidence={"window": self._window, "count": len(returns)},
            )
        else:
            signal = (
                "trending"
                if value >= MIDPOINT + self._band
                else (
                    "mean_reverting"
                    if value <= MIDPOINT - self._band
                    else "random_walk"
                )
            )
            strength = clip(abs(value - MIDPOINT) * HURST_STRENGTH_SCALE)
            confidence = clip(len(returns) / self._window * PERCENT)
            probability = max(0.0, min(1.0, value))
            card = self._runtime.card(
                tick,
                state,
                direction=0,
                strength=strength,
                confidence=confidence,
                probability=probability,
                signal=signal,
                directional=False,
                evidence={"hurst": value, "band": self._band, "count": len(returns)},
            )
        await self._context.publish(EVENT_OUT, card)
        self._emitted += 1

    async def snapshot(self):
        return {
            "runtime": self._runtime.snapshot(),
            "ticks": self._ticks,
            "emitted": self._emitted,
        }

    async def restore(self, state):
        if isinstance(state, dict):
            self._runtime.restore(state.get("runtime"))
            self._ticks = int(state.get("ticks", 0))
            self._emitted = int(state.get("emitted", 0))

    async def health_check(self):
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        # Owner 2026-08-22: the pulse carries 3 states — (1) an event was received,
        # (2) I am healthy, (3) no fresh result yet (still analyzing).
        if self._ticks == 0:
            return HealthStatus(state=HealthState.DEGRADED, message="NO_TICKS_YET",
                                details={"ticks": 0, "emitted": 0, "invalid": self._runtime.invalid})
        if self._emitted == 0:
            return HealthStatus(state=HealthState.HEALTHY, message="RECEIVING_OK_NO_RESULT_YET",
                                details={"ticks": self._ticks, "emitted": 0,
                                         "invalid": self._runtime.invalid,
                                         "status": "analyzing_wait_for_result"})
        return HealthStatus(state=HealthState.HEALTHY, message="OK_EMITTING",
                            details={"ticks": self._ticks, "emitted": self._emitted,
                                     "invalid": self._runtime.invalid,
                                     "status": "healthy_and_publishing"})
