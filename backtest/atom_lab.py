# -*- coding: utf-8 -*-
"""مختبر المعايرة — يشغّل الذرّات الحقيقية على بيانات تاريخية.

الغرض (أمر المالك): تغيير عتبة، عزل محلّل أو قسم، ومعرفة *لماذا* فشل —
بنفس الكود الحي، لا بمؤشرات مستقلة.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from backtest.historical_data import data_windows, load_real_m15, parse_day
from backtest.lab_sandbox import (
    lab_config,
    overlays_for,
    reset_all_lab_configs,
    reset_lab_config,
    save_lab_config,
)
from backtest.runner import BacktestRunner

_LOCK = threading.Lock()
_RUNNING = False
_HISTORY: list[dict[str, Any]] = []
_LAST: dict[str, Any] | None = None

SECTIONS: dict[str, dict[str, Any]] = {
    "analysis": {
        "ar": "التحليل",
        "atoms": [151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166],
        "why": "15 محلّل سريع/بطيء + الدمج 166",
    },
    "structure": {
        "ar": "البنية",
        "atoms": [200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210],
        "why": "سوينغ / كسر / BOS / CHOCH",
    },
    "liquidity": {
        "ar": "السيولة",
        "atoms": [250, 251, 252, 253, 254, 255, 260],
        "why": "برك / سويب / FVG",
    },
    "statistics": {
        "ar": "الإحصاء",
        "atoms": [300, 301, 302, 303, 304, 305, 306],
        "why": "وسط / انحراف / z-score",
    },
    "probability": {
        "ar": "الاحتمالات",
        "atoms": [351, 352, 353, 355, 359],
        "why": "نماذج احتمال مدموجة",
    },
    "strategy": {
        "ar": "الاستراتيجيات",
        "atoms": [400, 401, 402, 404, 405, 406],
        "why": "قواعد الدخول/الخروج",
    },
    "decision": {
        "ar": "القرار",
        "atoms": [451, 452, 453, 454, 455],
        "why": "تجميع → تقييم → فلتر",
    },
    "risk": {
        "ar": "المخاطر",
        "atoms": [500, 506, 507, 508],
        "why": "سقف / تعرّض / جلسة",
    },
    "news": {
        "ar": "الأخبار",
        "atoms": [615, 616],
        "why": "جسر الأخبار والتقويم",
    },
}

ANALYSTS = [
    {"id": 151, "key": "trend", "ar": "الاتجاه"},
    {"id": 152, "key": "momentum", "ar": "الزخم"},
    {"id": 153, "key": "volatility", "ar": "التذبذب"},
    {"id": 154, "key": "volume", "ar": "الحجم"},
    {"id": 155, "key": "spread", "ar": "السبريد"},
    {"id": 156, "key": "candle", "ar": "الشموع"},
    {"id": 157, "key": "gap", "ar": "الفجوات"},
    {"id": 158, "key": "session", "ar": "الجلسات"},
    {"id": 159, "key": "time", "ar": "أثر الوقت"},
    {"id": 160, "key": "correlation", "ar": "الارتباط"},
    {"id": 161, "key": "relative_strength", "ar": "القوة النسبية"},
    {"id": 162, "key": "velocity", "ar": "السرعة"},
    {"id": 163, "key": "acceleration", "ar": "التسارع"},
    {"id": 164, "key": "volume_quality", "ar": "جودة الحجم"},
    {"id": 165, "key": "noise", "ar": "الضوضاء"},
    {"id": 166, "key": "fusion", "ar": "الدمج"},
]

READY = {"READY", "DECISION_READY", "VALID"}
FAIL = {"NOT_READY", "STALE", "INVALID", "ERROR", "DORMANT"}
BLOCK_AR = {
    "REQUIRED_UNITS": "وحدات إلزامية ساكتة",
    "STALE": "التغذية متقادمة",
    "DEPTH": "العمق لم يكتمل",
    "IDENTITY": "هوية النطاق ناقصة",
    "CONFIDENCE": "بلا ثقة بعد",
    "CONFIDENCE_BELOW_THRESHOLD": "الثقة تحت العتبة",
    "STRENGTH_BELOW_THRESHOLD": "القوة تحت العتبة",
    "DEPTH_BELOW_REQUIRED": "العمق لم يكتمل",
    "NOT_READY": "غير جاهز",
    "ERROR": "خطأ",
    "INVALID": "غير صالح",
    "DORMANT": "خامل",
}


_NAME_CACHE: dict[int, str] | None = None


def _atom_names() -> dict[int, str]:
    global _NAME_CACHE
    if _NAME_CACHE is not None:
        return _NAME_CACHE
    root = Path(__file__).resolve().parent.parent / "atoms"
    out: dict[int, str] = {}
    if root.is_dir():
        for folder in root.glob("*/*"):
            if not folder.is_dir():
                continue
            try:
                aid = int(folder.name.split("_", 1)[0])
            except ValueError:
                continue
            out[aid] = folder.name.split("_", 1)[-1].replace("_", " ")
    _NAME_CACHE = out
    return out


def _all_section_ids() -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for spec in SECTIONS.values():
        for aid in spec["atoms"]:
            if aid not in seen:
                seen.add(aid)
                ids.append(int(aid))
    return ids


def catalog() -> dict[str, Any]:
    names = _atom_names()
    with _LOCK:
        hist = list(_HISTORY[-10:])
        last_id = (_LAST or {}).get("run_id")
        running = _RUNNING
    return {
        "ok": True,
        "running": running,
        "sections": {
            key: {
                "id": key,
                "ar": spec["ar"],
                "atoms": spec["atoms"],
                "why": spec["why"],
                "names": {str(aid): names.get(int(aid), str(aid)) for aid in spec["atoms"]},
            }
            for key, spec in SECTIONS.items()
        },
        "analysts": ANALYSTS,
        "data": [
            {"id": "okx", "label": "BTC-USDT · OKX · تيكات من OHLC · 15 يوم"},
            {"id": "yahoo", "label": "BTC-USD · Yahoo · تيكات من OHLC · 60 يوم"},
        ],
        "sandbox": True,
        "overrides": overlays_for(),
        "history": hist,
        "last_run_id": last_id,
        "windows": data_windows(),
        "live_approved": False,
    }


def _why(payload: dict[str, Any]) -> str | None:
    state = str(payload.get("analysis_state") or payload.get("state") or "").upper()
    block = str(payload.get("block_reason") or payload.get("reason") or "").upper()
    included = payload.get("included")
    current = payload.get("current_depth")
    required = payload.get("required_depth")
    if block:
        return BLOCK_AR.get(block, block)
    if state in FAIL:
        return BLOCK_AR.get(state, state)
    if included is False:
        if isinstance(current, (int, float)) and isinstance(required, (int, float)):
            return f"مستبعد من الدمج — العمق {current:.1f} من {required:.1f}"
        return "الوزن مستبعد من الدمج"
    if state and state not in READY:
        return state
    return None


def _trim(payload: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "symbol", "account_id", "broker", "state", "analysis_state",
        "direction", "score", "strength", "confidence", "current_depth",
        "required_depth", "weight", "included", "block_reason", "reason",
        "signal", "side", "timestamp", "timeframe", "path",
        "impact", "impact_level", "sentiment", "headline", "title", "currency",
    )
    out: dict[str, Any] = {}
    for key in keep:
        if key in payload and payload[key] is not None:
            value = payload[key]
            if isinstance(value, float):
                out[key] = round(value, 4)
            else:
                out[key] = value
    return out


class _LabRunner(BacktestRunner):
    def _setup_monitors(self) -> None:  # noqa: D401
        super()._setup_monitors()
        self.last_by_event: dict[str, dict[str, Any]] = {}
        self.failures: list[dict[str, Any]] = []

        def wrap(event_name: str):
            def on_payload(payload: dict, _event=event_name) -> None:
                if not isinstance(payload, dict):
                    return
                snap = _trim(payload)
                snap["event"] = _event
                self.last_by_event[_event] = snap
                reason = _why(payload)
                if reason:
                    self.failures.append({
                        "event": _event,
                        "reason": reason,
                        "state": snap.get("state") or snap.get("analysis_state"),
                        "symbol": snap.get("symbol"),
                        "at": snap.get("timestamp"),
                    })
            return on_payload

        seen: set[str] = set()
        for events in (
            ["analysis.trend.state", "analysis.momentum.state", "analysis.volatility.state",
             "analysis.volume.state", "analysis.spread.state", "analysis.candle.state",
             "analysis.gap.state", "analysis.session.state", "analysis.section.live",
             "analysis.raw.completed", "analysis.analysts.state"],
            ["market.structure.updated", "structure.cycle.collected", "structure.section.live",
             "structure.swing.state", "structure.bos.state", "structure.choch.state"],
            ["market.liquidity.updated", "liquidity.cycle.collected", "liquidity.section.live",
             "liquidity.fvg.state", "liquidity.sweep.state"],
            ["stats.cycle.collected", "stats.section.live"],
            ["probability.cycle.collected", "probability.section.live", "probability.merged.state",
             "probability.confidence.state"],
            ["strategy.trend.state", "strategy.cycle.collected", "strategy.section.live"],
            ["decision.aggregated.state", "decision.room.state", "decision.filter.state",
             "decision.section.live"],
            ["risk.unified.state", "risk.halt.requested"],
            ["feed.news.state", "news.calendar.state", "platform.news.state"],
        ):
            for name in events:
                if name in seen:
                    continue
                seen.add(name)
                self.bus.subscribe(name, wrap(name))


def _atom_ids(body: dict[str, Any]) -> list[int] | None:
    if body.get("atom_id") is not None:
        return [int(body["atom_id"])]
    if body.get("atom_ids"):
        return [int(x) for x in body["atom_ids"]]
    section = str(body.get("section") or "").strip().lower()
    if section == "full" or not section:
        return None
    if section not in SECTIONS:
        raise ValueError(f"قسم غير معروف: {section}")
    return list(SECTIONS[section]["atoms"])


def run_lab(body: dict[str, Any] | None = None) -> dict[str, Any]:
    global _RUNNING, _LAST
    body = body or {}
    with _LOCK:
        if _RUNNING:
            return {"ok": False, "error": "جولة قيد التشغيل — انتظر انتهائها"}
        _RUNNING = True
    try:
        source = str(body.get("source") or "okx")
        max_candles = int(body.get("max_candles") or 120)
        max_candles = max(20, min(max_candles, 500))
        try:
            ids = _atom_ids(body)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        started = time.time()
        try:
            stream = load_real_m15(source=source, max_candles=max_candles)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"تعذّر تحميل البيانات: {type(exc).__name__}: {exc}"}

        runner = _LabRunner()
        if ids is None:
            ids = _all_section_ids()
            isolate = "full"
        else:
            isolate = body.get("section") or f"atom:{ids[0] if len(ids) == 1 else ids}"
        # ١٠٣ يبني الشموع من التيك — نفس المسار الحي، بعدد فريمات محدود.
        load_ids = list(ids)
        if 103 not in load_ids:
            load_ids = [103, *load_ids]
        overlay = overlays_for(load_ids)
        loaded = runner.load_atoms(atom_ids=load_ids, config_overrides=overlay)
        if loaded == 0:
            return {"ok": False, "error": "لم تُحمَّل أي ذرّة — راجع المعرّفات"}

        runner.set_data(stream)
        try:
            result = runner.run()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"فشل التشغيل: {type(exc).__name__}: {exc}"}
        last_map = getattr(runner, "last_by_event", {})
        failures = getattr(runner, "failures", [])
        latest_fail: dict[str, dict[str, Any]] = {}
        for row in failures:
            latest_fail[row["event"]] = row

        units = []
        for event, snap in sorted(last_map.items()):
            reason = _why(snap) if snap else None
            state = str(snap.get("state") or snap.get("analysis_state") or "")
            units.append({
                "event": event,
                "state": state or "—",
                "ok": (not reason) and (state.upper() in READY or not state),
                "reason": reason or latest_fail.get(event, {}).get("reason"),
                "last": snap,
            })

        failing = [u for u in units if u["reason"]]
        report = {
            "ok": True,
            "run_id": result.get("run_id"),
            "isolate": isolate,
            "source": source,
            "symbol": stream.symbol,
            "timeframe": stream.timeframe,
            "from_ts": stream.first_ts,
            "to_ts": stream.last_ts,
            "candles": result.get("candle_count"),
            "ticks": result.get("tick_count"),
            "atoms_loaded": loaded,
            "atom_ids": result.get("atom_ids"),
            "duration_s": round(time.time() - started, 3),
            "stages": result.get("stages"),
            "units": units,
            "failing": failing,
            "fail_count": len(failing),
            "decisions": result.get("decisions"),
            "error": result.get("error") or None,
            "provenance": result.get("provenance"),
        }
        summary = {
            "run_id": report["run_id"],
            "isolate": isolate,
            "source": source,
            "candles": report["candles"],
            "atoms_loaded": loaded,
            "fail_count": report["fail_count"],
            "duration_s": report["duration_s"],
            "at": time.time(),
        }
        with _LOCK:
            _LAST = report
            _HISTORY.append(summary)
            del _HISTORY[:-12]
        return report
    finally:
        with _LOCK:
            _RUNNING = False


def compare() -> dict[str, Any]:
    if len(_HISTORY) < 2:
        return {"ok": False, "error": "يلزم تشغيلان للمقارنة"}
    a, b = _HISTORY[-2], _HISTORY[-1]
    return {
        "ok": True,
        "before": a,
        "after": b,
        "delta": {
            "fail_count": (b.get("fail_count") or 0) - (a.get("fail_count") or 0),
            "duration_s": round((b.get("duration_s") or 0) - (a.get("duration_s") or 0), 3),
            "candles": (b.get("candles") or 0) - (a.get("candles") or 0),
        },
    }


def last_result() -> dict[str, Any]:
    if _LAST is None:
        return {"ok": False, "error": "لا نتيجة بعد — شغّل جولة"}
    return _LAST


def handle_lab(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
    import re
    path = path.split("?", 1)[0].rstrip("/")
    if path in ("/gov/lab", "/gov/lab/catalog") and method == "GET":
        return catalog()
    if path == "/gov/lab/last" and method == "GET":
        return last_result()
    if path == "/gov/lab/compare" and method == "GET":
        return compare()
    m = re.fullmatch(r"/gov/lab/config/(\d+)", path)
    if m:
        aid = int(m.group(1))
        if method == "GET":
            return lab_config(aid)
        if method == "POST":
            return save_lab_config(aid, body or {})
    m = re.fullmatch(r"/gov/lab/config/(\d+)/reset", path)
    if m and method == "POST":
        return reset_lab_config(int(m.group(1)))
    if path == "/gov/lab/reset-overrides" and method == "POST":
        return reset_all_lab_configs()
    # لا تُمسك _LOCK هنا: run_lab يمسكها لحظات فقط، والجولة نفسها خارج القفل.
    if path in ("/gov/lab/run", "/gov/lab/isolate") and method == "POST":
        return run_lab(body or {})
    return {"ok": False, "error": f"مسار مختبر غير معروف: {path}"}
