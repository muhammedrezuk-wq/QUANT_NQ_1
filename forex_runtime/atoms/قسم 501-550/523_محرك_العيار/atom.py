from __future__ import annotations

from typing import Any

from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from shared.analysis_speed import MATCH_POINT, horizon_value
from shared.horizon_profile import generate, profile_active

ATOM_VERSION = "2.3.0"

EVENT_LEDGER = "risk.asset_ledger.state"
EVENT_COMMAND = "dial.command"
EVENT_OUT = "dial.profile.state"
EVENT_ACCOUNT = "platform.account.state"
EVENT_TIME = "SYS_SECOND"
EVENT_SETTINGS_STATE = "decision.settings.state"
#: Horizon-profile shadow (unified indicator paper v1.0 + migration phases
#: §61): generated and published for the dashboard/measurement -- never
#: applied to any atom until the owner's explicit activation command.
EVENT_HORIZON = "horizon.profile.state"

REASON_NOT_STARTED = "NOT_STARTED"

_KEY_SEP = "|"
_DIAL_MIN = 0.0
_DIAL_MAX = 100.0
_MID = 0.5
_FRAC_DP = 8
_SEC_DP = 3
_SCOPE_PARTS = 3


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _lerp(low: float, high: float, t: float) -> float:
    return low + (high - low) * t


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._brokers: dict[str, str] = {}
        self._dials: dict[str, float] = {}
        self._active: set[str] = set()
        self._cfg: dict[str, float] = {}
        self._default_dial = 50.0
        self._emits = 0
        self._last_key: float | None = None
        self._shadow_emits = 0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        raw = cfg.get("dials") if isinstance(cfg.get("dials"), dict) else {}
        for key, value in raw.items():
            dial = _to_float(value)
            if dial is not None:
                self._dials[str(key)] = _clamp(dial, _DIAL_MIN, _DIAL_MAX)
        self._default_dial = _clamp(float(cfg.get("default_dial", 50.0)), _DIAL_MIN, _DIAL_MAX)
        self._cfg = {
            "horizon_min_s": float(cfg.get("horizon_min_s", 180.0)),
            "horizon_max_s": float(cfg.get("horizon_max_s", 14400.0)),
            "horizon_shape": float(cfg.get("horizon_shape", 1.0)),
            "filter_min": float(cfg.get("filter_min", 0.2)),
            "filter_max": float(cfg.get("filter_max", 0.9)),
            "stop_min_frac": float(cfg.get("stop_min_frac", 0.001)),
            "stop_max_frac": float(cfg.get("stop_max_frac", 0.01)),
            "cadence_fast_s": float(cfg.get("cadence_fast_s", 5.0)),
            "cadence_slow_s": float(cfg.get("cadence_slow_s", 60.0)),
        }
        context.subscribe(EVENT_LEDGER, self._on_ledger)
        context.subscribe(EVENT_COMMAND, self._on_command)
        context.subscribe(EVENT_ACCOUNT, self._on_account)
        context.subscribe(EVENT_TIME, self._on_second)
        context.subscribe(EVENT_SETTINGS_STATE, self._on_settings_state)

    async def start(self) -> None:
        self._running = True
        if self._dials:
            await self._emit()
        await self._publish_shadow()

    async def _publish_shadow(self) -> None:
        """Profile shadow from the horizon key -- publish only, zero application (Phase 1-2 §61)."""
        if self._context is None:
            return
        horizon = horizon_value()
        try:
            profile = generate(horizon)
        except ValueError:
            return
        self._last_key = horizon
        self._shadow_emits += 1
        applies = profile_active()
        await self._context.publish(EVENT_HORIZON,
                                    {"shadow": not applies, "applies": applies,
                                     **profile})

    async def _refresh_from_keys(self) -> None:
        """Horizon key changed: republish the live dial profile (consumed
        by 581 -- stop/horizon/cadence) and the profile shadow together."""
        await self._emit()
        await self._publish_shadow()

    async def _on_second(self, payload: dict[str, Any]) -> None:
        if not self._running:
            return
        if horizon_value() != self._last_key:
            await self._refresh_from_keys()

    async def _on_settings_state(self, payload: dict[str, Any]) -> None:
        if not self._running:
            return
        await self._refresh_from_keys()

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _profile(self, key: str) -> dict[str, Any]:
        parts = key.split(_KEY_SEP, 2)
        account_id, broker, symbol = parts if len(parts) == _SCOPE_PARTS else ("", "", "")
        dial = self._dials.get(key)
        if dial is None:
            # The governed horizon key (four-keys paper: higher = narrower/
            # scalp) translates to this engine's own scale (higher =
            # longer horizon) as a DEVIATION from the neutral point
            # MATCH_POINT, added onto this engine's own local default
            # `default_dial` -- not on top of a hardcoded 50. At the
            # key's neutral value (50, the default before any approval)
            # the two formulas are byte-identical, so nothing changes
            # behaviorally as long as `default_dial` also stays at its
            # own default (50). (Found 2026-08-27: `default_dial` was
            # read from config and stored, but never actually used --
            # any non-50 value was silently ignored.) A manual CALIBRATE
            # command for one specific scope still outranks the key
            # (the individual override beats the master one).
            dial = _clamp(self._default_dial + (MATCH_POINT - horizon_value(account_id, symbol)),
                          _DIAL_MIN, _DIAL_MAX)
        x = _clamp(dial, _DIAL_MIN, _DIAL_MAX) / _DIAL_MAX
        cfg = self._cfg
        shape = cfg["horizon_shape"] if cfg["horizon_shape"] > 0.0 else 1.0
        horizon = _lerp(cfg["horizon_min_s"], cfg["horizon_max_s"], x ** shape)
        filter_strength = _lerp(cfg["filter_min"], cfg["filter_max"], x)
        stop_frac = _lerp(cfg["stop_min_frac"], cfg["stop_max_frac"], x)
        cadence = _lerp(cfg["cadence_fast_s"], cfg["cadence_slow_s"], x)
        return {
            "account_id": account_id, "broker": broker, "symbol": symbol, "dial": dial,
            "horizon_seconds": round(horizon, _SEC_DP),
            "filter_strength": round(filter_strength, _FRAC_DP),
            "stop_distance_frac": round(stop_frac, _FRAC_DP),
            "mgmt_cadence_s": round(cadence, _SEC_DP),
            "lot_bias": "large" if x < _MID else "small",
        }

    async def _emit(self) -> None:
        if self._context is None:
            return
        keys = sorted(set(self._dials) | self._active)
        profiles = [self._profile(key) for key in keys]
        self._emits += 1
        await self._context.publish(EVENT_OUT, {"profiles": profiles, "count": len(profiles)})

    async def _on_account(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict): return
        account = str(payload.get("account_id") or "").strip()
        broker = str(payload.get("broker") or "").strip()
        if account and broker: self._brokers[account] = broker

    async def _on_ledger(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        ledgers = payload.get("ledgers")
        if not isinstance(ledgers, list):
            return
        changed = False
        for led in ledgers:
            if not isinstance(led, dict):
                continue
            symbol = str(led.get("symbol") or "")
            if not symbol:
                continue
            account = str(led.get("account_id") or ""); broker = str(led.get("broker") or "") or self._brokers.get(account, "")
            if not account or not broker: continue
            key = _KEY_SEP.join((account, broker, symbol))
            if key not in self._active:
                self._active.add(key)
                changed = True
        if changed or self._active:
            await self._emit()

    async def _on_command(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "")
        dial = _to_float(payload.get("dial"))
        if not symbol or dial is None:
            return
        account = str(payload.get("account_id") or ""); broker = str(payload.get("broker") or "") or self._brokers.get(account, "")
        if not account or not broker: return
        key = _KEY_SEP.join((account, broker, symbol))
        self._dials[key] = _clamp(dial, _DIAL_MIN, _DIAL_MAX)
        await self._emit()

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "brokers": dict(self._brokers),
                "dials": dict(self._dials), "active": sorted(self._active)}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or not isinstance(state.get("dials", {}), dict):
            raise ValueError("INVALID_DIAL_ENGINE_STATE")
        self._brokers = {str(k): str(v) for k,v in (state.get("brokers") or {}).items()}
        self._dials = {str(k): float(v) for k,v in state.get("dials", {}).items()
                       if _to_float(v) is not None}
        self._active = {str(x) for x in state.get("active", []) if isinstance(x, str)}

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message=REASON_NOT_STARTED)
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="dials=%d active=%d emits=%d" % (len(self._dials), len(self._active), self._emits),
            details={"dials": len(self._dials), "active": len(self._active), "emits": self._emits,
                     "shadow_emits": self._shadow_emits, "horizon_key": self._last_key})
