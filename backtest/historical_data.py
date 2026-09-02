# -*- coding: utf-8 -*-
"""محمّل البيانات التاريخية الحقيقية داخل اليوم (Intraday).

يحمّل شموع M15 من Yahoo/OKX ويحوّلها لتيكات OHLC (فتح/قمة/قاع/إغلاق).
لا سعر مخترَع — الشموع في المختبر يبنيها ١٠٣ من التيك، كما الحي.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from backtest.data_contract import DataPoint, DataStream, DataProvenance, DataQuality, DataSource

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "historical"


_TF_SECONDS = {"M1": 60.0, "M5": 300.0, "M15": 900.0, "M30": 1800.0, "H1": 3600.0, "D1": 86400.0}


def ohlc_to_ticks(stream: DataStream) -> DataStream:
    """تيكات من OHLC الحقيقي — بلا سعر مخترَع.

    النظام الحي يشتغل تيكات؛ الشموع يبنيها ١٠٣ بعدد محدود.
    كل شمعة تصير حتى ٤ تيكات: فتح · قاع/قمة · قمة/قاع · إغلاق — أسعار الملف نفسها.
    """
    period = _TF_SECONDS.get(stream.timeframe, 900.0)
    ticks: list[DataPoint] = []
    seq = 0
    last_ts = 0.0
    for candle in stream.points:
        o, h, l, c = candle.open, candle.high, candle.low, candle.close
        if c >= o:
            prices = [o, l, h, c]
        else:
            prices = [o, h, l, c]
        uniq: list[float] = []
        for price in prices:
            if price > 0 and (not uniq or uniq[-1] != price):
                uniq.append(price)
        if not uniq:
            continue
        n = len(uniq)
        for i, price in enumerate(uniq):
            ts = float(candle.timestamp) + period * (i / n)
            if ts <= last_ts:
                ts = last_ts + 0.001
            last_ts = ts
            seq += 1
            ticks.append(DataPoint(
                timestamp=ts,
                symbol=candle.symbol,
                timeframe="tick",
                source=candle.source,
                open=price, high=price, low=price, close=price,
                volume=candle.volume / n,
                bid=price, ask=price,
                sequence=seq,
                quality=candle.quality,
            ))
    provenance = DataProvenance(
        original_source=stream.provenance.original_source if stream.provenance else stream.source,
        ingest_time=time.time(),
        transformations=list(stream.provenance.transformations if stream.provenance else []) + ["ohlc_to_ticks"],
        validation_passed=True,
    )
    return DataStream(
        symbol=stream.symbol,
        timeframe="tick",
        source=stream.source,
        points=ticks,
        provenance=provenance,
        quality=stream.quality,
    )


def parse_day(value: str | float | int | None, *, end: bool = False) -> float | None:
    """يوم أو طابع → epoch ثوانٍ UTC. نهاية اليوم = 23:59:59."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        return ts / 1000.0 if ts > 10_000_000_000 else ts
    text = str(value).strip()
    if not text:
        return None
    from datetime import datetime, timezone
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" in text:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    if end:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.timestamp()


def _slice_raw(
    raw: list[dict],
    max_candles: int | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> list[dict]:
    out = raw
    if start_ts is not None:
        out = [c for c in out if float(c["timestamp"]) >= float(start_ts)]
    if end_ts is not None:
        out = [c for c in out if float(c["timestamp"]) <= float(end_ts)]
    if max_candles and max_candles < len(out):
        out = out[-max_candles:]
    return out


def data_windows() -> dict[str, Any]:
    """حدود الملفات التاريخية — للوحة من–إلى."""
    out: dict[str, Any] = {}
    for key, name in (
        ("okx", "btcusd_m15_okx_15d.json"),
        ("yahoo", "btcusd_m15_yahoo_60d.json"),
    ):
        path = DATA_DIR / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        candles = data.get("candles") or []
        if not candles:
            continue
        first, last = candles[0], candles[-1]
        out[key] = {
            "count": len(candles),
            "symbol": data.get("symbol"),
            "first_ts": float(first["timestamp"]),
            "last_ts": float(last["timestamp"]),
            "first": first.get("datetime") or data.get("first"),
            "last": last.get("datetime") or data.get("last"),
            "note": "OHLC تاريخي للمعايرة — مو تيك سي تريدر الحي",
        }
    return out


def load_real_m15(
    source: str = "yahoo",
    symbol: str | None = None,
    max_candles: int | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> DataStream:
    """تحميل شموع M15 ثم تحويلها لتيكات OHLC — مسار المختبر = مسار الحي."""
    if source == "yahoo":
        candles = _load_yahoo(max_candles=max_candles, symbol=symbol, start_ts=start_ts, end_ts=end_ts)
    elif source == "okx":
        candles = _load_okx(max_candles=max_candles, symbol=symbol, start_ts=start_ts, end_ts=end_ts)
    elif source == "both":
        candles = _load_both(max_candles=max_candles, symbol=symbol, start_ts=start_ts, end_ts=end_ts)
    else:
        raise ValueError(f"مصدر غير معروف: {source} — استخدم yahoo/okx/both")
    return ohlc_to_ticks(candles)


def _load_yahoo(
    max_candles: int | None = None,
    symbol: str | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> DataStream:
    path = DATA_DIR / "btcusd_m15_yahoo_60d.json"
    if not path.exists():
        raise FileNotFoundError(f"ملف Yahoo غير موجود: {path}")
    
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_candles = _slice_raw(data["candles"], max_candles, start_ts, end_ts)
    
    sym = symbol or data.get("symbol", "BTC-USD")
    points = _candles_to_points(raw_candles, sym, "M15", "yahoo_finance")
    
    return DataStream(
        symbol=sym,
        timeframe="M15",
        source="yahoo_finance",
        points=points,
        provenance=DataProvenance(
            original_source="yahoo_finance",
            ingest_time=time.time(),
            transformations=[],
            validation_passed=True,
        ),
        quality=DataQuality.COMPLETE,
    )


def _load_okx(
    max_candles: int | None = None,
    symbol: str | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> DataStream:
    path = DATA_DIR / "btcusd_m15_okx_15d.json"
    if not path.exists():
        raise FileNotFoundError(f"ملف OKX غير موجود: {path}")
    
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_candles = _slice_raw(data["candles"], max_candles, start_ts, end_ts)
    
    sym = symbol or data.get("symbol", "BTC-USDT")
    points = _candles_to_points(raw_candles, sym, "M15", "okx_exchange")
    
    return DataStream(
        symbol=sym,
        timeframe="M15",
        source="okx_exchange",
        points=points,
        provenance=DataProvenance(
            original_source="okx_exchange",
            ingest_time=time.time(),
            transformations=[],
            validation_passed=True,
        ),
        quality=DataQuality.COMPLETE,
    )


def _load_both(
    max_candles: int | None = None,
    symbol: str | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> DataStream:
    """يضم Yahoo + OKX بالترتيب الزمني — يلغي التكرار."""
    yahoo = _load_yahoo(symbol=symbol or "BTCUSDT", start_ts=start_ts, end_ts=end_ts)
    okx = _load_okx(symbol=symbol or "BTCUSDT", start_ts=start_ts, end_ts=end_ts)
    
    # ضم بالترتيب الزمني — إلغاء تكرار الطوابع
    seen_ts = set()
    merged = []
    for p in sorted(yahoo.points + okx.points, key=lambda x: x.timestamp):
        if p.timestamp not in seen_ts:
            seen_ts.add(p.timestamp)
            merged.append(p)
    
    if max_candles and max_candles < len(merged):
        merged = merged[-max_candles:]
    
    return DataStream(
        symbol=symbol or "BTCUSDT",
        timeframe="M15",
        source="yahoo+okx",
        points=merged,
        provenance=DataProvenance(
            original_source="yahoo_finance+okx_exchange",
            ingest_time=time.time(),
            transformations=["merge_deduplicated"],
            validation_passed=True,
        ),
        quality=DataQuality.COMPLETE,
    )


def _candles_to_points(
    candles: list[dict], symbol: str, timeframe: str, source: str
) -> list[DataPoint]:
    """تحويل شموع خام إلى DataPoints — بدون تعديل أو توسيع."""
    points = []
    for i, c in enumerate(candles):
        ts = float(c["timestamp"])
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        vol = float(c.get("volume", 0))
        
        # لا نولّد بيانات — كل حقل كما جاء من المصدر
        points.append(DataPoint(
            timestamp=ts,
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            open=o,
            high=h,
            low=l,
            close=cl,
            volume=vol,
            bid=cl,       # لا bid حقيقي من الشموع —近似
            ask=cl,       # لا ask حقيقي من الشموع —近似
            sequence=i,
            quality="complete" if vol > 0 else "no_volume",
        ))
    return points


def to_tick_payload(point: DataPoint) -> dict[str, Any]:
    """تحويل DataPoint إلى payload تيك للذرّات — مع كل الحقول المطلوبة."""
    return {
        "account_id": "backtest_001",
        "broker": "backtest",
        "symbol": point.symbol,
        "price": point.close,
        "bid": point.bid if point.bid > 0 else point.close,
        "ask": point.ask if point.ask > 0 else point.close,
        "source_timestamp": point.timestamp,
        "exchange_timestamp": point.timestamp,
        "timestamp": point.timestamp,
        "volume": point.volume,
        "sequence": point.sequence,
        "timeframe": "tick",
        "period_start": point.timestamp,
        "cycle_id": f"tick:{point.symbol}:{point.sequence}",
    }


def to_candle_payload(point: DataPoint) -> dict[str, Any]:
    """تحويل DataPoint إلى payload شمعة للذرّات — مع كل الحقول المطلوبة."""
    return {
        "symbol": point.symbol,
        "account_id": "backtest_001",
        "broker": "backtest",
        "timestamp": point.timestamp,
        "source_timestamp": point.timestamp,
        "exchange_timestamp": point.timestamp,
        "open": point.open,
        "high": point.high,
        "low": point.low,
        "close": point.close,
        "volume": point.volume,
        "timeframe": point.timeframe,
        "source": point.source,
        "sequence": point.sequence,
        "period_start": point.timestamp,
    }
