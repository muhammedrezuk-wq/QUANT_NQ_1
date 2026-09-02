# -*- coding: utf-8 -*-
"""مستقبل بيانات WebSocket — يجلب البيانات التاريخية من cTrader للبكتست.

يدعم طريقتين:
1. WebSocket مباشر من cTrader (عبر الجسر الموجود)
2. ملف محلي (CSV/JSON) — للاستخدام بدون cTrader متصل

عقد البيانات الواردة (JSON عبر WebSocket):
  {"type": "tick", "symbol": "EURUSD", "ts": 1700000000.123,
   "bid": 1.08500, "ask": 1.08510, "volume": 1000}

  {"type": "history", "symbol": "EURUSD", "tf": "M1",
   "bars": [{"ts": ..., "o": ..., "h": ..., "l": ..., "c": ..., "v": ...}]}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from backtest.models import Candle, Tick

log = logging.getLogger("backtest.data_feed")


class DataFeed:
    """واجهة موحّدة لمصادر البيانات — WebSocket أو ملف محلي."""

    def __init__(self):
        self._ticks: list[Tick] = []
        self._candles: list[Candle] = []
        self._on_tick: Callable[[Tick], None] | None = None
        self._on_candle: Callable[[Candle], None] | None = None

    @property
    def ticks(self) -> list[Tick]:
        return self._ticks

    @property
    def candles(self) -> list[Candle]:
        return self._candles

    @property
    def total_ticks(self) -> int:
        return len(self._ticks)


async def load_from_websocket(
    host: str, port: int, symbol: str,
    on_tick: Callable[[Tick], None] | None = None,
    timeout_s: float = 30.0,
) -> DataFeed:
    """جلب البيانات من cTrader عبر WebSocket.

    يرسل أمر "history" ويجمع التيكات/الشموع حتى timeout.
    """
    try:
        import websockets
    except ImportError:
        raise ImportError("websockets غير مثبّت — pip install websockets")

    feed = DataFeed()
    feed._on_tick = on_tick

    uri = f"ws://{host}:{port}"
    log.info(f"اتصال WebSocket للبكتست: {uri} — الرمز {symbol}")

    try:
        async with websockets.connect(uri, open_timeout=5.0) as ws:
            # طلب البيانات التاريخية
            request = json.dumps({
                "cmd": "backtest_history",
                "symbol": symbol,
                "timeframe": "M1",
                "max_bars": 10000,
            })
            await ws.send(request)
            start = time.monotonic()

            while time.monotonic() - start < timeout_s:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    msg = json.loads(raw)
                    _process_message(feed, msg)
                except asyncio.TimeoutError:
                    break
                except Exception as exc:
                    log.warning(f"خطأ في رسالة WebSocket: {exc}")
                    continue
    except Exception as exc:
        log.error(f"فشل اتصال WebSocket: {exc}")
        raise

    log.info(f"جُمعت {feed.total_ticks} تيك و {len(feed.candles)} شمعة")
    return feed


async def load_from_file(path: str | Path) -> DataFeed:
    """تحميل بيانات من ملف محلي (CSV أو JSON).

    CSV format: timestamp,bid,ask,volume
    JSON format: {"ticks": [...]} أو {"candles": [...]}
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"ملف البيانات غير موجود: {path}")

    feed = DataFeed()
    ext = p.suffix.lower()

    if ext == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if "ticks" in data:
            for t in data["ticks"]:
                tick = Tick(
                    symbol=t.get("symbol", ""),
                    timestamp=float(t.get("ts", t.get("timestamp", 0))),
                    bid=float(t.get("bid", 0)),
                    ask=float(t.get("ask", 0)),
                    volume=float(t.get("volume", t.get("vol", 0))),
                )
                feed._ticks.append(tick)
        if "candles" in data:
            for c in data["candles"]:
                candle = Candle(
                    symbol=c.get("symbol", ""),
                    timestamp=float(c.get("ts", c.get("timestamp", 0))),
                    open=float(c.get("o", c.get("open", 0))),
                    high=float(c.get("h", c.get("high", 0))),
                    low=float(c.get("l", c.get("low", 0))),
                    close=float(c.get("c", c.get("close", 0))),
                    volume=float(c.get("v", c.get("volume", 0))),
                )
                feed._candles.append(candle)

    elif ext == ".csv":
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        header = lines[0].lower() if lines else ""
        for line in lines[1 if header.startswith("timestamp") else 0:]:
            parts = line.strip().split(",")
            if len(parts) >= 3:
                try:
                    tick = Tick(
                        symbol="",
                        timestamp=float(parts[0]),
                        bid=float(parts[1]),
                        ask=float(parts[2]) if len(parts) > 2 else float(parts[1]),
                        volume=float(parts[3]) if len(parts) > 3 else 0,
                    )
                    feed._ticks.append(tick)
                except (ValueError, IndexError):
                    continue
    else:
        raise ValueError(f"صيغة غير مدعومة: {ext} — استخدم .json أو .csv")

    log.info(f"حُمّلت {feed.total_ticks} تيك و {len(feed.candles)} شمعة من {path}")
    return feed


async def generate_synthetic_data(
    symbol: str = "EURUSD",
    num_ticks: int = 10000,
    start_price: float = 1.0850,
    volatility: float = 0.0001,
    start_time: float | None = None,
    tick_interval_s: float = 0.1,
) -> DataFeed:
    """توليد بيانات اصطناعية للاختبار (مسيرة عشوائية)."""
    import random
    feed = DataFeed()
    t = start_time or time.time() - num_ticks * tick_interval_s
    price = start_price
    spread = 0.00010  # 1 pip

    for i in range(num_ticks):
        change = random.gauss(0, volatility)
        price = max(price + change, 0.0001)
        bid = price - spread / 2
        ask = price + spread / 2
        feed._ticks.append(Tick(
            symbol=symbol,
            timestamp=t,
            bid=round(bid, 5),
            ask=round(ask, 5),
            volume=random.randint(100, 5000),
        ))
        t += tick_interval_s

    # بناء شموع M1 من التيكات
    if feed._ticks:
        candle_seconds = 60
        current_candle: Candle | None = None
        for tick in feed._ticks:
            candle_ts = int(tick.timestamp / candle_seconds) * candle_seconds
            if current_candle is None or current_candle.timestamp != candle_ts:
                if current_candle is not None:
                    feed._candles.append(current_candle)
                current_candle = Candle(
                    symbol=symbol, timestamp=candle_ts,
                    open=tick.mid, high=tick.mid, low=tick.mid, close=tick.mid,
                )
            current_candle.close = tick.mid
            current_candle.high = max(current_candle.high, tick.mid)
            current_candle.low = min(current_candle.low, tick.mid)
            current_candle.volume += tick.volume
            current_candle.tick_count += 1
        if current_candle is not None:
            feed._candles.append(current_candle)

    log.info(f"تُولّدت {feed.total_ticks} تيك اصطناعي ({symbol})")
    return feed


def _process_message(feed: DataFeed, msg: dict[str, Any]) -> None:
    """معالجة رسالة واحدة من WebSocket."""
    msg_type = msg.get("type", msg.get("cmd", ""))

    if msg_type in ("tick", "t"):
        tick = Tick(
            symbol=msg.get("s", msg.get("symbol", "")),
            timestamp=float(msg.get("ts", msg.get("timestamp", 0))),
            bid=float(msg.get("bid", msg.get("b", 0))),
            ask=float(msg.get("ask", msg.get("a", 0))),
            volume=float(msg.get("volume", msg.get("v", 0))),
        )
        feed._ticks.append(tick)
        if feed._on_tick:
            feed._on_tick(tick)

    elif msg_type in ("history", "bars"):
        for bar in msg.get("bars", msg.get("data", [])):
            candle = Candle(
                symbol=bar.get("s", msg.get("symbol", "")),
                timestamp=float(bar.get("ts", bar.get("timestamp", 0))),
                open=float(bar.get("o", bar.get("open", 0))),
                high=float(bar.get("h", bar.get("high", 0))),
                low=float(bar.get("l", bar.get("low", 0))),
                close=float(bar.get("c", bar.get("close", 0))),
                volume=float(bar.get("v", bar.get("volume", 0))),
            )
            feed._candles.append(candle)
