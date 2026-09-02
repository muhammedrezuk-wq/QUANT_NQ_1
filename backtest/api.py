# -*- coding: utf-8 -*-
"""نقاط API للبكتست — تُربط بخادم الحوكمة (governance/server.py).

توفّر:
  - POST /backtest/run      — تشغيل جولة باك تست
  - GET  /backtest/status   — حالة البكتست الحالي
  - GET  /backtest/result   — نتيجة آخر جولة
  - GET  /backtest/strategies — قائمة الاستراتيجيات المتاحة
  - POST /backtest/stop     — إيقاف البكتست الحالي
  - POST /backtest/data/upload — رفع ملف بيانات
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from backtest.engine import BacktestEngine, run_backtest
from backtest.models import BacktestConfig, BacktestResult
from backtest.strategies import list_strategies

log = logging.getLogger("backtest.api")

# ═══ الحالة العامة ═══
_current_engine: BacktestEngine | None = None
_current_task: asyncio.Task | None = None
_last_result: BacktestResult | None = None
_results_history: list[dict[str, Any]] = []  # ملخصات فقط
_data_dir: Path | None = None


def init(data_root: Path | None = None) -> None:
    """تهيئة وحدة البكتست — تُستدعى عند بدء الخادم."""
    global _data_dir
    if data_root:
        _data_dir = data_root / "backtest"
        _data_dir.mkdir(parents=True, exist_ok=True)
    log.info("وحدة البكتست جاهزة")


def handle_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """معالج الطلبات — يُستدعى من governance/server.py.

    method: "GET" أو "POST"
    path: "/backtest/run" أو "/backtest/status" ...
    body: محتوى الطلب (POST فقط)
    """
    if path == "/backtest/strategies":
        return _handle_strategies()
    elif path == "/backtest/status":
        return _handle_status()
    elif path == "/backtest/result":
        return _handle_result()
    elif path == "/backtest/history":
        return _handle_history()
    elif path == "/backtest/run" and method == "POST":
        return _handle_run(body or {})
    elif path == "/backtest/stop" and method == "POST":
        return _handle_stop()
    elif path == "/backtest/data/upload" and method == "POST":
        return _handle_upload(body or {})
    # ═══ المختبر ═══
    elif path == "/backtest/lab/indicators":
        return _handle_lab_indicators()
    elif path == "/backtest/lab/status":
        return _handle_lab_status()
    elif path == "/backtest/lab/run" and method == "POST":
        return _handle_lab_run(body or {})
    elif path == "/backtest/lab/toggle" and method == "POST":
        return _handle_lab_toggle(body or {})
    elif path == "/backtest/lab/params" and method == "POST":
        return _handle_lab_params(body or {})
    elif path == "/backtest/lab/test_single" and method == "POST":
        return _handle_lab_test_single(body or {})
    elif path == "/backtest/lab/calibrate" and method == "POST":
        return _handle_lab_calibrate(body or {})
    elif path == "/backtest/lab/news" and method == "POST":
        return _handle_lab_news(body or {})
    elif path == "/backtest/lab/reset" and method == "POST":
        return _handle_lab_reset()
    else:
        return {"error": "مسار غير معروف", "path": path}


def _handle_strategies() -> dict[str, Any]:
    """قائمة الاستراتيجيات المتاحة."""
    return {"strategies": list_strategies()}


def _handle_status() -> dict[str, Any]:
    """حالة البكتست الحالي."""
    if _current_engine and _current_task and not _current_task.done():
        return {
            "status": "running",
            "run_id": _current_engine.result.run_id,
            "strategy": _current_engine.config.strategy_name,
            "symbol": _current_engine.config.symbol,
            "started_at": _current_engine.result.started_at,
            "trades_so_far": len(_current_engine.result.trades),
            "ticks_count": _current_engine.result.ticks_count,
        }
    return {"status": "idle"}


def _handle_result() -> dict[str, Any]:
    """نتيجة آخر جولة."""
    if _last_result:
        return _last_result.to_dict()
    return {"status": "no_result", "message": "لا توجد نتيجة بعد"}


def _handle_history() -> dict[str, Any]:
    """ملخص آخر النتائج."""
    return {"results": _results_history[-20:]}


def _handle_run(params: dict[str, Any]) -> dict[str, Any]:
    """تشغيل جولة باك تست جديدة."""
    global _current_engine, _current_task, _last_result

    # فحص إن كان هناك باك تست قيد التشغيل
    if _current_task and not _current_task.done():
        return {"error": "يوجد باك تست قيد التشغيل — أوقفه أولاً أو انتظر انتهائه"}

    config = BacktestConfig(
        symbol=params.get("symbol", "EURUSD"),
        initial_capital=float(params.get("initial_capital", 100_000)),
        lot_size=float(params.get("lot_size", 0.01)),
        commission_per_lot=float(params.get("commission_per_lot", 0.0)),
        slippage_pips=float(params.get("slippage_pips", 0.0)),
        max_open_trades=int(params.get("max_open_trades", 1)),
        strategy_name=params.get("strategy", "ma_crossover"),
        strategy_params=params.get("params", {}),
        ws_host=params.get("ws_host", "127.0.0.1"),
        ws_port=int(params.get("ws_port", 0)),
    )

    _current_engine = BacktestEngine(config)

    # تشغيل في background
    data_file = params.get("data_file")
    _current_task = asyncio.ensure_future(_run_async(_current_engine, data_file))

    return {
        "status": "started",
        "run_id": _current_engine.result.run_id,
        "strategy": config.strategy_name,
        "symbol": config.symbol,
    }


async def _run_async(engine: BacktestEngine, data_file: str | None = None) -> None:
    """تشغيل البكتست بشكل غير متزامن."""
    global _last_result
    from backtest.data_feed import load_from_file, generate_synthetic_data

    feed = None
    if data_file and _data_dir:
        fp = _data_dir / data_file
        if fp.exists():
            feed = await load_from_file(str(fp))

    result = await engine.run(feed)
    _last_result = result

    # حفظ ملخص في التاريخ
    _results_history.append({
        "run_id": result.run_id,
        "strategy": result.config.strategy_name,
        "symbol": result.config.symbol,
        "status": result.status,
        "total_trades": result.total_trades,
        "win_rate": round(result.win_rate, 4),
        "net_pnl": round(result.net_pnl, 2),
        "max_drawdown": round(result.max_drawdown, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 4),
        "duration_s": round(result.duration_s, 3),
        "finished_at": result.finished_at,
    })


def _handle_stop() -> dict[str, Any]:
    """إيقاف البكتست الحالي."""
    global _current_engine
    if _current_engine:
        _current_engine.stop()
        return {"status": "stopping", "run_id": _current_engine.result.run_id}
    return {"status": "idle", "message": "لا يوجد باك تست قيد التشغيل"}


def _handle_upload(params: dict[str, Any]) -> dict[str, Any]:
    """رفع ملف بيانات للبكتست."""
    if not _data_dir:
        return {"error": "مجلد البيانات غير مُعدّ"}

    filename = params.get("filename", f"data_{uuid.uuid4().hex[:8]}.json")
    content = params.get("content", "")
    if not content:
        return {"error": "لا يوجد محتوى"}

    filepath = _data_dir / filename
    filepath.write_text(content, encoding="utf-8")
    return {"status": "uploaded", "filename": filename, "path": str(filepath)}


# ═══════════════════════════════════════════════════════════════════════════════
# معالجات المختبر
# ═══════════════════════════════════════════════════════════════════════════════

_lab = None

def _get_lab():
    global _lab
    if _lab is None:
        from backtest.lab import IndicatorLab
        _lab = IndicatorLab()
    return _lab


def _handle_lab_indicators() -> dict[str, Any]:
    """قائمة كل المؤشرات + حالتها."""
    from backtest.indicators.indicators import list_indicators
    lab = _get_lab()
    indicators = list_indicators()
    status = lab.get_status()
    return {"indicators": indicators, "status": status}


def _handle_lab_status() -> dict[str, Any]:
    """حالة المختبر."""
    lab = _get_lab()
    return {"status": lab.get_status()}


def _handle_lab_run(params: dict[str, Any]) -> dict[str, Any]:
    """تشغيل المختبر — كل المؤشرات المشغّلة على بيانات."""
    import asyncio
    from backtest.data_feed import generate_synthetic_data
    from backtest.models import Candle

    lab = _get_lab()
    symbol = params.get("symbol", "EURUSD")
    num_candles = int(params.get("num_candles", 500))

    # توليد بيانات اصطناعية (أو جلب من WebSocket/ملف)
    async def _gen():
        feed = await generate_synthetic_data(symbol=symbol, num_ticks=num_candles * 10)
        return feed.candles[:num_candles] if feed.candles else []

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    candles = loop.run_until_complete(_gen())
    if not candles:
        return {"error": "لا توجد بيانات"}

    result = lab.run(candles, symbol=symbol)
    return result.to_dict()


def _handle_lab_toggle(params: dict[str, Any]) -> dict[str, Any]:
    """تشغيل/إطفاء مؤشر."""
    lab = _get_lab()
    name = params.get("name", "")
    enabled = params.get("enabled", None)
    if name not in lab.slots:
        return {"error": f"مؤشر غير معروف: {name}"}
    if enabled is not None:
        lab.enable(name, bool(enabled))
    else:
        lab.toggle(name)
    return {"status": "ok", "name": name, "enabled": lab.slots[name].enabled}


def _handle_lab_params(params: dict[str, Any]) -> dict[str, Any]:
    """تعيين معاملات مؤشر."""
    lab = _get_lab()
    name = params.get("name", "")
    new_params = params.get("params", {})
    if name not in lab.slots:
        return {"error": f"مؤشر غير معروف: {name}"}
    lab.set_params(name, new_params)
    return {"status": "ok", "name": name, "params": lab.slots[name].indicator.params}


def _handle_lab_test_single(params: dict[str, Any]) -> dict[str, Any]:
    """اختبار مؤشر واحد."""
    import asyncio
    from backtest.data_feed import generate_synthetic_data

    lab = _get_lab()
    name = params.get("name", "rsi")
    symbol = params.get("symbol", "EURUSD")
    num_candles = int(params.get("num_candles", 500))
    custom_params = params.get("params", None)

    async def _gen():
        feed = await generate_synthetic_data(symbol=symbol, num_ticks=num_candles * 10)
        return feed.candles[:num_candles] if feed.candles else []

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    candles = loop.run_until_complete(_gen())
    if not candles:
        return {"error": "لا توجد بيانات"}

    return lab.test_single(name, candles, custom_params)


def _handle_lab_calibrate(params: dict[str, Any]) -> dict[str, Any]:
    """معايرة مؤشر — parameter sweep."""
    import asyncio
    from backtest.data_feed import generate_synthetic_data

    lab = _get_lab()
    name = params.get("name", "rsi")
    symbol = params.get("symbol", "EURUSD")
    num_candles = int(params.get("num_candles", 500))

    async def _gen():
        feed = await generate_synthetic_data(symbol=symbol, num_ticks=num_candles * 10)
        return feed.candles[:num_candles] if feed.candles else []

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    candles = loop.run_until_complete(_gen())
    if not candles:
        return {"error": "لا توجد بيانات"}

    result = lab.calibrate(name, candles)
    return {
        "name": result.name,
        "optimal_params": result.optimal_params,
        "win_rate": round(result.win_rate, 4),
        "avg_return": round(result.avg_return, 6),
        "sharpe": round(result.sharpe, 4),
        "trades_tested": result.trades_tested,
        "param_sweep": result.param_sweep,
    }


def _handle_lab_news(params: dict[str, Any]) -> dict[str, Any]:
    """اختبار تأثير سلسلة أخبار."""
    import asyncio
    from backtest.data_feed import generate_synthetic_data

    lab = _get_lab()
    symbol = params.get("symbol", "EURUSD")
    news_events = params.get("events", [])
    num_candles = int(params.get("num_candles", 1000))

    if not news_events:
        return {"error": "لا يوجد أحداث إخبارية"}

    async def _gen():
        feed = await generate_synthetic_data(symbol=symbol, num_ticks=num_candles * 10)
        return feed.candles[:num_candles] if feed.candles else []

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    candles = loop.run_until_complete(_gen())
    if not candles:
        return {"error": "لا توجد بيانات"}

    results = lab.test_news_impact(news_events, candles)
    return {"results": results}


def _handle_lab_reset() -> dict[str, Any]:
    """إعادة تهيئة المختبر."""
    lab = _get_lab()
    lab.reset_all()
    return {"status": "ok", "message": "تمت إعادة تهيئة كل المؤشرات"}
