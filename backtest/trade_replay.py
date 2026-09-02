# -*- coding: utf-8 -*-
"""باك تست رسمي — نفس ذرّات التداول على تيكات تاريخية، تنفيذ ورقي.

المختبر يعاير ويعزل. هنا نشوف: عدّلت استراتيجية، شغّلتها على بيانات،
كيف كانت الصفقات والـ PnL تظهر — بلا أمر للمنصّة (لا ٩٠١/٥٧٦/٦٠١).
"""
from __future__ import annotations

import threading
import time
from typing import Any

from pathlib import Path

from backtest.historical_data import data_windows, load_real_m15, parse_day
from backtest.lab_sandbox import overlays_for
from backtest.metrics import compute_metrics, build_equity_curve
from backtest.models import Side, Trade
from backtest.news_window import news_on_range
from backtest.runner import BacktestRunner

ROOT = Path(__file__).resolve().parent.parent
PNL_DIR = ROOT / "var" / "backtest" / "pnl"

_LOCK = threading.Lock()
_RUNNING = False
_LAST: dict[str, Any] | None = None
_HISTORY: list[dict[str, Any]] = []

# مسار التداول الحقيقي — بلا جسور أوامر حيّة.
TRADE_ATOMS: list[int] = [
    103,
    *range(151, 167),
    *range(200, 211),
    250, 251, 252, 253, 254, 255, 260,
    *range(300, 307),
    351, 352, 353, 354, 355, 356, 357, 358, 359,
    400, 401, 402, 404, 405, 406, 407, 408, 409, 410, 411, 412, 413,
    450, 451, 452, 453, 454, 455, 456, 457, 458,
    460, 461, 462, 463, 464, 466, 467,
]

BLOCKED = {576, 601, 901, 622, 618}


class PaperBook:
    """دفتر ورقي: قرار النظام (buy/sell) → صفقة محاكاة على سعر التيك."""

    def __init__(self, capital: float = 10_000.0, lot: float = 0.01) -> None:
        self.capital = float(capital)
        self.lot = float(lot)
        self.equity = self.capital
        self.last_price = 0.0
        self.last_ts = 0.0
        self._open: Trade | None = None
        self.trades: list[Trade] = []
        self.decisions: list[dict[str, Any]] = []
        self.sides = {"buy": 0, "sell": 0, "wait": 0, "other": 0}
        self._next_id = 0
        self._symbol = ""

    def on_tick(self, ts: float, price: float, symbol: str = "") -> None:
        if price > 0:
            self.last_price = price
            self.last_ts = ts
        if symbol:
            self._symbol = symbol

    def on_decision(self, payload: dict[str, Any]) -> None:
        side = str(payload.get("decision_side") or payload.get("signal") or "").strip().lower()
        if side in ("buy", "sell", "wait"):
            self.sides[side] += 1
        else:
            self.sides["other"] += 1
        ts = float(payload.get("source_timestamp") or payload.get("timestamp") or self.last_ts or 0.0)
        price = self.last_price
        snap = {
            "side": side or "—",
            "reason": payload.get("reason"),
            "score": payload.get("score"),
            "confidence": payload.get("confidence"),
            "symbol": payload.get("symbol"),
            "at": ts,
        }
        self.decisions.append(snap)
        if len(self.decisions) > 400:
            del self.decisions[:-200]
        if side not in ("buy", "sell") or price <= 0:
            return
        wanted = Side.BUY if side == "buy" else Side.SELL
        if self._open is not None and self._open.side == wanted:
            return
        if self._open is not None:
            self._close(price, ts, f"عكس → {side}")
        self._open_pos(wanted, price, ts, str(payload.get("reason") or side))

    def close_end(self) -> None:
        if self._open is not None and self.last_price > 0:
            self._close(self.last_price, self.last_ts, "نهاية الجولة")

    def _open_pos(self, side: Side, price: float, ts: float, reason: str) -> None:
        self._next_id += 1
        self._open = Trade(
            id=self._next_id,
            symbol=str(getattr(self, "_symbol", "") or ""),
            side=side,
            entry_price=price,
            entry_time=ts,
            size=self.lot,
            reason=reason,
        )

    def _close(self, price: float, ts: float, reason: str) -> None:
        trade = self._open
        if trade is None:
            return
        trade.close(price, ts, reason=reason)
        self.equity += trade.net_pnl
        self.trades.append(trade)
        self._open = None

    def report(self) -> dict[str, Any]:
        curve = build_equity_curve(self.trades, self.capital, equity_sample_interval=1)
        metrics = compute_metrics(self.trades, curve, self.capital)
        open_row = None
        if self._open is not None:
            open_row = {
                "id": self._open.id,
                "side": self._open.side.value,
                "entry_price": round(self._open.entry_price, 4),
                "entry_time": self._open.entry_time,
                "size": self._open.size,
                "status": "OPEN",
            }
        return {
            "capital": self.capital,
            "lot": self.lot,
            "final_equity": round(self.equity, 4),
            "metrics": {
                "total_trades": metrics.get("total_trades", 0),
                "winning_trades": metrics.get("winning_trades", 0),
                "losing_trades": metrics.get("losing_trades", 0),
                "win_rate": round(float(metrics.get("win_rate") or 0), 4),
                "net_pnl": round(float(metrics.get("net_pnl") or 0), 4),
                "profit_factor": round(float(metrics.get("profit_factor") or 0), 4)
                if metrics.get("profit_factor") != float("inf") else None,
                "max_drawdown": round(float(metrics.get("max_drawdown") or 0), 4),
                "max_drawdown_pct": round(float(metrics.get("max_drawdown_pct") or 0), 4),
                "sharpe_ratio": round(float(metrics.get("sharpe_ratio") or 0), 4),
                "return_pct": round(float(metrics.get("return_pct") or 0), 4),
            },
            "sides": dict(self.sides),
            "open": open_row,
            "trades": [
                {
                    "id": t.id,
                    "side": t.side.value,
                    "entry_price": round(t.entry_price, 4),
                    "exit_price": round(t.exit_price or 0, 4),
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "pnl": round(t.pnl, 4),
                    "net_pnl": round(t.net_pnl, 4),
                    "reason": t.reason,
                    "duration_s": round(t.duration_s, 1),
                    "status": t.status.value,
                }
                for t in self.trades[-80:]
            ],
            "decisions": self.decisions[-40:],
            "equity_curve": [
                {"t": round(ep.timestamp, 3), "e": round(ep.equity, 2)}
                for ep in curve[-400:]
            ],
        }


class _TradeRunner(BacktestRunner):
    def __init__(self, book: PaperBook) -> None:
        super().__init__()
        self.book = book
        self.last_by_event: dict[str, dict[str, Any]] = {}

    def _setup_monitors(self) -> None:
        super()._setup_monitors()

        def on_tick(payload: dict) -> None:
            if not isinstance(payload, dict):
                return
            try:
                price = float(payload.get("price") or payload.get("close") or 0)
                ts = float(payload.get("timestamp") or 0)
            except (TypeError, ValueError):
                return
            self.book.on_tick(ts, price, str(payload.get("symbol") or ""))

        def on_resolved(payload: dict) -> None:
            if isinstance(payload, dict):
                self.last_by_event["decision.resolved.state"] = {
                    k: payload.get(k) for k in (
                        "decision_side", "signal", "reason", "score",
                        "confidence", "symbol", "origin",
                    ) if payload.get(k) is not None
                }
                self.book.on_decision(payload)

        def keep(event: str):
            def on_payload(payload: dict, _e=event) -> None:
                if not isinstance(payload, dict):
                    return
                self.last_by_event[_e] = {
                    k: payload.get(k) for k in (
                        "state", "analysis_state", "signal", "direction",
                        "score", "confidence", "reason", "gate_state",
                        "reject_reason", "ready", "side", "decision_side",
                    ) if payload.get(k) is not None
                }
            return on_payload

        self.bus.subscribe("market.tick.validated", on_tick)
        self.bus.subscribe("decision.resolved.state", on_resolved)
        for name in (
            "strategy.section.live", "decision.aggregated.state",
            "decision.gate.passed", "decision.gate.blocked",
            "decision.dispatch.state", "analysis.section.live",
        ):
            self.bus.subscribe(name, keep(name))


def catalog() -> dict[str, Any]:
    with _LOCK:
        hist = list(_HISTORY[-10:])
        running = _RUNNING
        last_id = (_LAST or {}).get("run_id")
    return {
        "ok": True,
        "running": running,
        "sandbox": True,
        "atoms": TRADE_ATOMS,
        "blocked": sorted(BLOCKED),
        "data": [
            {"id": "okx", "label": "BTC-USDT · OKX · تيكات من OHLC · 15 يوم"},
            {"id": "yahoo", "label": "BTC-USD · Yahoo · تيكات من OHLC · 60 يوم"},
        ],
        "history": hist,
        "last_run_id": last_id,
        "windows": data_windows(),
        "promote": promote_status(),
        "live_approved": False,
        "note": "ذرّات النظام على تيكات تاريخية · دفتر ورقي · بلا أمر للمنصّة · مو اعتماد حي",
    }


def run_trade(body: dict[str, Any] | None = None) -> dict[str, Any]:
    global _RUNNING, _LAST
    body = body or {}
    with _LOCK:
        if _RUNNING:
            return {"ok": False, "error": "جولة قيد التشغيل — انتظر انتهائها"}
        _RUNNING = True
    try:
        source = str(body.get("source") or "okx")
        max_candles = max(20, min(int(body.get("max_candles") or 80), 400))
        capital = float(body.get("capital") or 10_000.0)
        lot = float(body.get("lot") or 0.01)
        start_ts = parse_day(body.get("from_date") or body.get("start_ts"))
        end_ts = parse_day(body.get("to_date") or body.get("end_ts"), end=True)
        if capital <= 0 or lot <= 0:
            return {"ok": False, "error": "رأس المال واللوت لازم أكبر من صفر"}

        started = time.time()
        try:
            stream = load_real_m15(
                source=source, max_candles=max_candles,
                start_ts=start_ts, end_ts=end_ts,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"تعذّر تحميل البيانات: {type(exc).__name__}: {exc}"}
        if len(stream) == 0:
            return {"ok": False, "error": "ما في شموع بهالفترة — غيّر من–إلى"}

        news = news_on_range(stream.first_ts, stream.last_ts)
        book = PaperBook(capital=capital, lot=lot)
        runner = _TradeRunner(book)
        overlay = overlays_for(TRADE_ATOMS)
        loaded = runner.load_atoms(atom_ids=TRADE_ATOMS, config_overrides=overlay)
        if loaded == 0:
            return {"ok": False, "error": "لم تُحمَّل أي ذرّة"}
        runner.set_data(stream)
        runner.set_news(list(news.get("news") or []) + list(news.get("calendar") or []))
        try:
            result = runner.run()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"فشل التشغيل: {type(exc).__name__}: {exc}"}
        book.close_end()
        paper = book.report()
        last_map = getattr(runner, "last_by_event", {})
        report = {
            "ok": True,
            "kind": "trade",
            "run_id": result.get("run_id"),
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
            "error": result.get("error") or None,
            "paper": paper,
            "last": last_map,
            "news": news,
            "provenance": result.get("provenance"),
            "live_untouched": True,
            "live_approved": False,
        }
        report["pnl_file"] = _write_pnl(str(report["run_id"]), report)
        summary = {
            "run_id": report["run_id"],
            "source": source,
            "ticks": report["ticks"],
            "trades": paper["metrics"]["total_trades"],
            "net_pnl": paper["metrics"]["net_pnl"],
            "win_rate": paper["metrics"]["win_rate"],
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


def _write_pnl(run_id: str, report: dict[str, Any]) -> dict[str, str]:
    import json as _json
    PNL_DIR.mkdir(parents=True, exist_ok=True)
    paper = report.get("paper") or {}
    payload = {
        "run_id": run_id,
        "symbol": report.get("symbol"),
        "source": report.get("source"),
        "from_ts": report.get("from_ts"),
        "to_ts": report.get("to_ts"),
        "metrics": paper.get("metrics"),
        "trades": paper.get("trades"),
        "live_approved": False,
        "kind": "paper",
    }
    json_path = PNL_DIR / f"{run_id}.json"
    csv_path = PNL_DIR / f"{run_id}.csv"
    json_path.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["id,side,entry_price,exit_price,entry_time,exit_time,pnl,net_pnl,reason"]
    for t in paper.get("trades") or []:
        lines.append(
            f"{t.get('id')},{t.get('side')},{t.get('entry_price')},{t.get('exit_price')},"
            f"{t.get('entry_time')},{t.get('exit_time')},{t.get('pnl')},{t.get('net_pnl')},"
            f"\"{str(t.get('reason') or '').replace('\"', '')}\""
        )
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (PNL_DIR / "last.json").write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path)}


def promote_status() -> dict[str, Any]:
    import os
    import sys
    windows = sys.platform.startswith("win")
    vault = (ROOT / "runtime" / "secrets.enc").is_file()
    key = (ROOT / "runtime" / "device.key").is_file()
    return {
        "live_approved": False,
        "can_promote": False,
        "windows": windows,
        "vault_present": vault,
        "device_key_present": key,
        "reason": (
            "هالصندوق لينكس بلا خزنة — الترقية لديمو/حي على ويندوز بعد "
            "سي تريدر + FIX + ديمو مثبت. ما في زر هون بيبعت أمر للمنصّة."
            if not windows else
            (
                "ويندوز حاضر. الخزنة ناقصة — عبّي runtime/secrets.enc من قسم الأمان ثم ديمو قبل الحي."
                if not vault else
                "الخزنة موجودة. الترقية: شغّل زر الفوركس الموحّد على ديمو أولًا. هالـAPI ما بيفتح الحي."
            )
        ),
        "windows_bat": "أزرار التشغيل/تشغيل الفوركس الموحد.bat",
        "blocked_here": sorted(BLOCKED),
    }


def last_result() -> dict[str, Any]:
    if _LAST is None:
        return {"ok": False, "error": "لا نتيجة بعد — شغّل جولة"}
    return _LAST


def last_pnl() -> dict[str, Any]:
    path = PNL_DIR / "last.json"
    if not path.is_file():
        return {"ok": False, "error": "لا ملف PnL بعد — شغّل جولة"}
    import json as _json
    try:
        return {"ok": True, "file": str(path), **_json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"تعذّر قراءة PnL: {type(exc).__name__}"}


def handle_backtest(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
    path = path.split("?", 1)[0].rstrip("/")
    if path in ("/gov/backtest", "/gov/backtest/catalog") and method == "GET":
        return catalog()
    if path == "/gov/backtest/last" and method == "GET":
        return last_result()
    if path == "/gov/backtest/pnl" and method == "GET":
        return last_pnl()
    if path == "/gov/backtest/promote" and method == "GET":
        return {"ok": True, **promote_status()}
    if path == "/gov/backtest/news" and method == "GET":
        # الجسم فاضي على GET — آخر جولة أو نوافذ الملفات
        last = _LAST or {}
        return news_on_range(last.get("from_ts"), last.get("to_ts"))
    if path == "/gov/backtest/run" and method == "POST":
        return run_trade(body or {})
    if path == "/gov/backtest/promote" and method == "POST":
        st = promote_status()
        return {"ok": False, "error": st["reason"], **st}
    if path == "/gov/backtest/news" and method == "POST":
        body = body or {}
        start = parse_day(body.get("from_date") or body.get("start_ts"))
        end = parse_day(body.get("to_date") or body.get("end_ts"), end=True)
        return news_on_range(start, end)
    return {"ok": False, "error": f"مسار باك تست غير معروف: {path}"}
