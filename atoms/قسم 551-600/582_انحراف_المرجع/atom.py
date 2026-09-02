from __future__ import annotations

import math
from collections import deque
from typing import Any

import clock
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus

ATOM_VERSION = "2.0.0"

EVENT_CTRADER = "feed.ctrader.tick"
EVENT_MT5 = "feed.mt5.tick"
EVENT_SPECS = "market.symbol_specs"
EVENT_PULSE = "SYS_SECOND"
EVENT_OUT = "execution.reference_divergence.state"

NORMAL = "NORMAL"
LEVEL_OFFSET_ONLY = "LEVEL_OFFSET_ONLY"
EXPECTED_DIVERGENCE = "EXPECTED_DIVERGENCE"
SUSPICIOUS_DIVERGENCE = "SUSPICIOUS_DIVERGENCE"
CLOCK_INVALID = "CLOCK_INVALID"
STALE = "STALE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# مراقبة فقط — لا BLOCK/HALT من ٥٨٢. ٥٧٨ تبقى على status==SYNCED
# للعيّنات القابلة للمقارنة حتى لا يُوقف التعرّض من تصنيف مراقبة.
_OBSERVE = {NORMAL, LEVEL_OFFSET_ONLY, EXPECTED_DIVERGENCE, SUSPICIOUS_DIVERGENCE}

_STAMP_KEYS = ("source_timestamp", "exchange_timestamp", "timestamp", "broker_timestamp")
_EPS = 1e-12
_MATCH_FRAC = 0.05


def num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def source_stamp(tick: dict[str, Any] | None) -> float | None:
    """طابع مجال المصدر. القيمة None لا تسقط إلى صفر ولا إلى clock.now()."""
    if not isinstance(tick, dict):
        return None
    for key in _STAMP_KEYS:
        if key not in tick:
            continue
        value = num(tick.get(key))
        if value is not None:
            return value
    return None


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (len(sorted_vals) - 1) * pct
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return sorted_vals[lo]
    weight = rank - lo
    return sorted_vals[lo] * (1.0 - weight) + sorted_vals[hi] * weight


class _SymbolState:
    __slots__ = (
        "ct", "mt", "ct_heard_mono", "mt_heard_mono",
        "ct_prev_price", "mt_prev_price",
        "ct_pending", "mt_pending",
        "last_move", "window", "ratio_abs",
    )

    def __init__(self) -> None:
        self.ct: dict[str, Any] | None = None
        self.mt: dict[str, Any] | None = None
        self.ct_heard_mono = 0.0
        self.mt_heard_mono = 0.0
        self.ct_prev_price: float | None = None
        self.mt_prev_price: float | None = None
        self.ct_pending = False
        self.mt_pending = False
        self.last_move: dict[str, Any] | None = None
        self.window: deque[dict[str, Any]] = deque()
        self.ratio_abs: list[float] = []


class Atom(AtomBase):
    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._symbols: dict[str, _SymbolState] = {}
        self._points: dict[str, float] = {}
        self._max_dev = 50.0
        self._max_age_s = 5.0
        self._window_s = 0.15
        self._window_ticks = 20
        self._window_seconds = 30.0
        self._suspicious_repeats = 3
        self._expected_return_abs = 0.0005
        self._suspicious_return_abs = 0.005
        self._updates = 0
        self._compared = 0
        self._waiting = 0
        self._stale = 0
        self._clock_invalid = 0
        self._now = 0.0

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._max_dev = float(cfg.get("max_deviation_points", 50))
        self._max_age_s = float(cfg.get("max_age_s", 5))
        self._window_s = float(cfg["alignment_window_s"])
        self._window_ticks = max(2, int(cfg.get("window_ticks", 20)))
        self._window_seconds = float(cfg.get("window_seconds", 30))
        self._suspicious_repeats = max(1, int(cfg.get("suspicious_repeats", 3)))
        self._expected_return_abs = float(cfg.get("expected_return_abs", 0.0005))
        self._suspicious_return_abs = float(cfg.get("suspicious_return_abs", 0.005))
        context.subscribe(EVENT_CTRADER, self._on_ct)
        context.subscribe(EVENT_MT5, self._on_mt)
        context.subscribe(EVENT_SPECS, self._on_specs)
        context.subscribe(EVENT_PULSE, self._on_pulse)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        await self.stop()

    def _state(self, symbol: str) -> _SymbolState:
        slot = self._symbols.get(symbol)
        if slot is None:
            slot = _SymbolState()
            self._symbols[symbol] = slot
        return slot

    async def _on_specs(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        rows = payload.get("symbols")
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or not row.get("symbol"):
                continue
            point = num(row.get("point"))
            if point and point > 0:
                self._points[str(row["symbol"])] = point

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict):
            return
        stamp = num(payload.get("official_time"))
        if stamp is None:
            return
        self._now = stamp
        for symbol in list(self._symbols):
            await self._publish(symbol, ingest=False)

    async def _on_ct(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict) or not payload.get("symbol"):
            return
        symbol = str(payload["symbol"])
        slot = self._state(symbol)
        slot.ct = dict(payload)
        slot.ct_heard_mono = clock.mono()
        slot.ct_pending = True
        await self._publish(symbol, ingest=True)

    async def _on_mt(self, payload: dict[str, Any]) -> None:
        if not self._running or not isinstance(payload, dict) or not payload.get("symbol"):
            return
        symbol = str(payload["symbol"])
        slot = self._state(symbol)
        slot.mt = dict(payload)
        slot.mt_heard_mono = clock.mono()
        slot.mt_pending = True
        await self._publish(symbol, ingest=True)

    def _heard_age(self, tick: dict[str, Any] | None, heard_mono: float) -> float | None:
        if tick is None:
            return None
        received = num(tick.get("received_at"))
        if received is not None and received > 0:
            return clock.now() - received
        if heard_mono:
            return clock.mono() - heard_mono
        return None

    def _clock_layer(self, tick: dict[str, Any] | None, heard_mono: float) -> str:
        if tick is None:
            return INSUFFICIENT_DATA
        if source_stamp(tick) is None:
            return CLOCK_INVALID
        age = self._heard_age(tick, heard_mono)
        if age is None or age < 0 or age > self._max_age_s:
            return STALE
        return "VALID"

    def _price(self, tick: dict[str, Any] | None) -> float | None:
        if not isinstance(tick, dict):
            return None
        value = num(tick.get("price"))
        if value is not None and value > 0:
            return value
        bid, ask = num(tick.get("bid")), num(tick.get("ask"))
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            return (bid + ask) / 2.0
        return None

    def _caps(self, slot: _SymbolState) -> tuple[float, float]:
        expected = self._expected_return_abs
        suspicious = self._suspicious_return_abs
        if len(slot.ratio_abs) >= 20:
            ordered = sorted(slot.ratio_abs)
            expected = max(_percentile(ordered, 0.95), expected)
            suspicious = max(_percentile(ordered, 0.99), suspicious)
        return expected, suspicious

    def _classify(self, symbol: str, slot: _SymbolState, *, ingest: bool) -> dict[str, Any]:
        ct, mt = slot.ct, slot.mt
        clock_ct = self._clock_layer(ct, slot.ct_heard_mono)
        clock_mt = self._clock_layer(mt, slot.mt_heard_mono)
        cp, mp = self._price(ct), self._price(mt)
        # مجالان مختلفان: SendingTime (UTC) ≠ ساعة وسيط MT5 (غالباً UTC+3
        # ملبوسة كـ epoch). طرحهما يعطي ~10800 ويُقرأ كـ«تأخير ٣ ساعات».
        # الفجوة عبر المجال تُترك فارغة. عمر الاستقبال على نفس الجهاز هو
        # الرقم القابل للمقارنة. إزاحة الوسيط تُعلَن كما قاسها ٦١٨.
        ct_recv = num(ct.get("received_at")) if isinstance(ct, dict) else None
        mt_recv = num(mt.get("received_at")) if isinstance(mt, dict) else None
        receipt_gap = (
            abs(ct_recv - mt_recv)
            if ct_recv is not None and mt_recv is not None
            else None
        )
        broker_offset = num(mt.get("broker_clock_offset_s")) if isinstance(mt, dict) else None
        base = {
            "clock_ct": clock_ct,
            "clock_mt": clock_mt,
            "timestamp_gap_s": None,
            "receipt_gap_s": receipt_gap,
            "broker_clock_offset_s": broker_offset,
        }

        pair_ready = bool(ingest and slot.ct_pending and slot.mt_pending)
        if pair_ready:
            slot.ct_pending = False
            slot.mt_pending = False

        if cp is None or mp is None or ct is None or mt is None:
            return {**base, "classification": INSUFFICIENT_DATA}

        if clock_ct == CLOCK_INVALID or clock_mt == CLOCK_INVALID:
            return {**base, "classification": CLOCK_INVALID}

        if clock_ct == STALE or clock_mt == STALE:
            return {**base, "classification": STALE}

        point = self._points.get(symbol, 1.0) or 1.0
        level_offset = mp - cp
        level_points = level_offset / point
        base["level_offset"] = level_offset
        base["level_points"] = level_points

        if pair_ready:
            if slot.ct_prev_price is None or slot.mt_prev_price is None:
                slot.ct_prev_price = cp
                slot.mt_prev_price = mp
                body = {
                    **base,
                    "classification": INSUFFICIENT_DATA,
                    "reason": "need_previous_print_for_move",
                }
                slot.last_move = None
                return body

            prev_ct, prev_mt = slot.ct_prev_price, slot.mt_prev_price
            ref_move = cp - prev_ct
            broker_move = mp - prev_mt
            slot.ct_prev_price = cp
            slot.mt_prev_price = mp

            ref_return = ref_move / prev_ct if abs(prev_ct) > _EPS else None
            broker_return = broker_move / prev_mt if abs(prev_mt) > _EPS else None
            movement_ratio = (
                None
                if ref_return is None or broker_return is None
                else broker_return - ref_return
            )
            norm_ref = ref_move / cp if abs(cp) > _EPS else 0.0
            norm_br = broker_move / cp if abs(cp) > _EPS else 0.0
            norm_delta = abs(norm_br - norm_ref)
            same_dir = (ref_move * broker_move > 0) or (
                abs(ref_move) <= _EPS and abs(broker_move) <= _EPS
            )

            expected_cap, suspicious_cap = self._caps(slot)
            match_cap = expected_cap * _MATCH_FRAC
            if movement_ratio is not None:
                slot.ratio_abs.append(abs(movement_ratio))
                if len(slot.ratio_abs) > 400:
                    slot.ratio_abs = slot.ratio_abs[-400:]

            move = {
                **base,
                "reference_move": ref_move,
                "broker_move": broker_move,
                "normalized_move_ref": norm_ref,
                "normalized_move_broker": norm_br,
                "normalized_move_delta": norm_delta,
                "movement_delta": broker_move - ref_move,
                "movement_ratio": movement_ratio,
                "direction_agreement": same_dir,
            }

            if norm_delta <= match_cap and same_dir:
                move["classification"] = (
                    LEVEL_OFFSET_ONLY if abs(level_offset) > _EPS else NORMAL
                )
            elif norm_delta >= suspicious_cap:
                move["classification"] = SUSPICIOUS_DIVERGENCE
                move["score"] = min(1.0, norm_delta / max(suspicious_cap, _EPS))
            else:
                move["classification"] = EXPECTED_DIVERGENCE

            now_mono = clock.mono()
            slot.window.append({"classification": move["classification"], "at": now_mono})
            while slot.window and (
                len(slot.window) > self._window_ticks
                or now_mono - slot.window[0]["at"] > self._window_seconds
            ):
                slot.window.popleft()
            hits = sum(1 for row in slot.window if row["classification"] == SUSPICIOUS_DIVERGENCE)
            move["frequency"] = hits
            move["window_ticks"] = len(slot.window)
            move["duration"] = (
                0.0 if len(slot.window) < 2 else slot.window[-1]["at"] - slot.window[0]["at"]
            )
            slot.last_move = dict(move)
            return move

        if slot.last_move is not None:
            kept = dict(slot.last_move)
            kept.update(base)
            kept["level_offset"] = level_offset
            kept["level_points"] = level_points
            return kept
        return {**base, "classification": INSUFFICIENT_DATA, "reason": "need_previous_print_for_move"}

    async def _publish(self, symbol: str, *, ingest: bool) -> None:
        if self._context is None:
            return
        slot = self._state(symbol)
        body = self._classify(symbol, slot, ingest=ingest)
        classification = str(body.get("classification") or INSUFFICIENT_DATA)
        self._updates += 1
        if classification == INSUFFICIENT_DATA:
            self._waiting += 1
        elif classification == STALE:
            self._stale += 1
        elif classification == CLOCK_INVALID:
            self._clock_invalid += 1
        elif classification in _OBSERVE:
            self._compared += 1

        status = "SYNCED" if classification in _OBSERVE else classification

        ct, mt = slot.ct or {}, slot.mt or {}
        cp, mp = self._price(slot.ct), self._price(slot.mt)
        ct_stamp, mt_stamp = source_stamp(slot.ct), source_stamp(slot.mt)
        ages = [
            self._heard_age(slot.ct, slot.ct_heard_mono),
            self._heard_age(slot.mt, slot.mt_heard_mono),
        ]
        point = self._points.get(symbol, 1.0) or 1.0
        payload: dict[str, Any] = {
            "symbol": symbol,
            "status": status,
            "classification": classification,
            "observe_only": True,
            "read_only": True,
            "source_ref": "ctrader",
            "source_broker": "mt5",
            "clock_domain_ref": "ctrader",
            "clock_domain_broker": "mt5",
            "reference_price": cp,
            "broker_price": mp,
            "reference_timestamp": ct_stamp,
            "broker_timestamp": mt_stamp,
            "deviation_points": (None if cp is None or mp is None else (mp - cp) / point),
            "timestamp_gap_s": None,
            "receipt_gap_s": body.get("receipt_gap_s"),
            "broker_clock_offset_s": body.get("broker_clock_offset_s"),
            "sample_ages_s": ages,
            "max_deviation_points": self._max_dev,
            "alignment_window_s": self._window_s,
            "max_age_s": self._max_age_s,
            "account_id": ct.get("account_id") or mt.get("account_id"),
            "broker": mt.get("broker") or ct.get("broker"),
            "window_ticks": body.get("window_ticks", len(slot.window)),
            "duration": body.get("duration", 0.0),
        }
        for key in (
            "clock_ct", "clock_mt", "level_offset", "level_points",
            "reference_move", "broker_move", "normalized_move_ref",
            "normalized_move_broker", "normalized_move_delta",
            "movement_delta", "movement_ratio", "direction_agreement",
            "frequency", "score", "reason",
        ):
            if key in body:
                payload[key] = body[key]
        await self._context.publish(EVENT_OUT, payload)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        details = {
            "version": ATOM_VERSION,
            "updates": self._updates,
            "compared": self._compared,
            "waiting": self._waiting,
            "stale": self._stale,
            "clock_invalid": self._clock_invalid,
            "alignment_window_s": self._window_s,
            "max_deviation_points": self._max_dev,
            "max_age_s": self._max_age_s,
            "observe_only": True,
            "pulse_lag_s": (None if not self._now else round(clock.now() - self._now, 3)),
        }
        if not self._compared:
            return HealthStatus(
                state=HealthState.DEGRADED,
                message=(
                    "NO_COMPARISON_YET: compared=0 waiting=%d stale=%d clock_invalid=%d"
                    % (self._waiting, self._stale, self._clock_invalid)
                ),
                details=details,
            )
        return HealthStatus(
            state=HealthState.HEALTHY,
            message="compared=%d clock_invalid=%d stale=%d" % (
                self._compared, self._clock_invalid, self._stale),
            details=details,
        )
