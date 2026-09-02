# -*- coding: utf-8 -*-
"""عقد البيانات الموحّد — Unified Data Contract.

هذا العقد هو مصدر الحقيقة الوحيد لكل البيانات في النظام.
يدخل المحرك الحي ويدخل محرك الباك تست بنفس الشكل.
الفرق الوحيد: مصدر البيانات (历史ي vs حي).

كل بيانات تمر عبر النظام يجب أن تحمل:
  - source: من أين جاءت
  - timestamp: وقت الحدوث
  - symbol: الرمز
  - timeframe: الفريم
  - quality: الجودة (مكتمل/ناقص/متأخر)
  - provenance: سلسلة المصادر
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterator


class DataSource(str, Enum):
    """مصدر البيانات."""
    CTRADER = "ctrader"           # cTrader bridge
    MT5 = "mt5"                   # MetaTrader 5
    BINANCE = "binance"           # Binance API
    MEXC = "mexc"                 # MEXC API
    CSV = "csv"                   # ملف CSV
    JSON = "json"                 # ملف JSON
    SYNTHETIC = "synthetic"       # بيانات اصطناعية (للاختبار فقط)
    REPLAY = "replay"             # إعادة تشغيل تاريخي


class DataQuality(str, Enum):
    """جودة البيانات."""
    COMPLETE = "complete"         # مكتمل — لا gaps
    PARTIAL = "partial"           # ناقص — فيه gaps
    STALE = "stale"               # متأخر — آخر نقطة قديمة
    DEGRADED = "degraded"         # متدهور — أخطاء في القراءة
    UNKNOWN = "unknown"           # غير معروف


class TimeFrame(str, Enum):
    """الفريمات الرسمية."""
    TICK = "tick"
    S5 = "5s"
    S15 = "15s"
    S30 = "30s"
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"


@dataclass(frozen=True, slots=True)
class DataPoint:
    """نقطة بيانات واحدة — العقد الأساسي.

    كل نقطة تحمل هويتها الكاملة: من أين جاءت، متى، جودتها.
    ممنوع إنشاء نقطة بدون source أو timestamp.
    """
    timestamp: float           # epoch seconds — وقت الحدوث الفعلي
    symbol: str                # الرمز
    timeframe: str             # الفريم
    source: str                # DataSource value
    # OHLCV
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    # Bid/Ask (للتكّات)
    bid: float = 0.0
    ask: float = 0.0
    # جودة
    quality: str = "complete"
    # provenance
    sequence: int = 0          # رقم تسلسلي (إن وجد)
    broker_id: str = ""        # معرّف الوسيط
    extra: str = ""            # JSON إضافي

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.close if self.close > 0 else self.open

    @property
    def spread(self) -> float:
        return self.ask - self.bid if (self.ask > 0 and self.bid > 0) else 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        """بصمة فريدة — لكل نقطة بصمة لا تتكرر."""
        raw = f"{self.timestamp}|{self.symbol}|{self.timeframe}|{self.source}|{self.sequence}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class DataProvenance:
    """سلسلة مصادر البيانات — تتبع كامل."""
    original_source: str       # المصدر الأول
    ingest_time: float         # وقت الاستلام
    transformations: list[str] = field(default_factory=list)  # تحويلات طُبّقت
    validation_passed: bool = True
    gaps_detected: int = 0
    last_sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DataStream:
    """تيّار بيانات — يحتوي نقاط + provenance + حالة.

    هذا هو العقد الذي يدخل كل المحركات:
    - المحرك الحي يقرأ منه
    - محرك الباك تست يقرأ منه
    - كل محلل يقرأ منه
    """
    symbol: str
    timeframe: str
    source: str
    points: list[DataPoint] = field(default_factory=list)
    provenance: DataProvenance | None = None
    quality: str = "complete"
    _position: int = 0  # موقع القراءة الحالي

    def __len__(self) -> int:
        return len(self.points)

    def __iter__(self) -> Iterator[DataPoint]:
        return iter(self.points)

    @property
    def first_ts(self) -> float:
        return self.points[0].timestamp if self.points else 0.0

    @property
    def last_ts(self) -> float:
        return self.points[-1].timestamp if self.points else 0.0

    @property
    def duration_s(self) -> float:
        return self.last_ts - self.first_ts if len(self.points) >= 2 else 0.0

    def detect_gaps(self, expected_interval_s: float) -> list[tuple[float, float]]:
        """كشف الفجوات الزمنية — نقاط ناقصة."""
        gaps: list[tuple[float, float]] = []
        for i in range(1, len(self.points)):
            dt = self.points[i].timestamp - self.points[i - 1].timestamp
            if dt > expected_interval_s * 2.5:  # أكثر من 2.5x المتوقع
                gaps.append((self.points[i - 1].timestamp, self.points[i].timestamp))
        return gaps

    def validate(self) -> dict[str, Any]:
        """فحص صحة التيّار."""
        result: dict[str, Any] = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": self.source,
            "count": len(self.points),
            "quality": self.quality,
            "valid": True,
            "errors": [],
        }
        if not self.points:
            result["valid"] = False
            result["errors"].append("EMPTY_STREAM")
            return result

        # فحص الترتيب الزمني
        for i in range(1, len(self.points)):
            if self.points[i].timestamp < self.points[i - 1].timestamp:
                result["valid"] = False
                result["errors"].append(f"TIME_ORDER_VIOLATION at index {i}")
                break

        # فحص الأسعار
        for i, p in enumerate(self.points):
            if p.high < p.low and p.high > 0 and p.low > 0:
                result["valid"] = False
                result["errors"].append(f"HIGH_LESS_THAN_LOW at index {i}")
                break
            if p.high > 0 and p.low > 0:
                if p.open > p.high * 1.01 or p.open < p.low * 0.99:
                    result["valid"] = False
                    result["errors"].append(f"OPEN_OUT_OF_RANGE at index {i}")
                    break

        # فحص الفجوات
        if self.timeframe != "tick":
            tf_seconds = _tf_to_seconds(self.timeframe)
            if tf_seconds > 0:
                gaps = self.detect_gaps(tf_seconds)
                if gaps:
                    result["gaps"] = len(gaps)
                    if result["quality"] == "complete":
                        result["quality"] = "partial"

        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": self.source,
            "count": len(self.points),
            "quality": self.quality,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "duration_s": self.duration_s,
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }


def _tf_to_seconds(tf: str) -> float:
    """تحويل فريم لثواني."""
    mapping = {
        "tick": 0, "5s": 5, "15s": 15, "30s": 30,
        "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
        "H1": 3600, "H4": 14400, "D1": 86400,
        "W1": 604800,
    }
    return mapping.get(tf, 0)


def create_stream_from_candles(
    symbol: str,
    candles: list[Any],  # list of Candle or similar
    source: str = "unknown",
    timeframe: str = "M1",
) -> DataStream:
    """إنشاء DataStream من قائمة شموع — الجسر بين القديم والجديد."""
    points = []
    for c in candles:
        ts = getattr(c, 'timestamp', 0) or c.get('timestamp', 0) if isinstance(c, dict) else 0
        o = getattr(c, 'open', 0) or (c.get('open', 0) if isinstance(c, dict) else 0)
        h = getattr(c, 'high', 0) or (c.get('high', 0) if isinstance(c, dict) else 0)
        l = getattr(c, 'low', 0) or (c.get('low', 0) if isinstance(c, dict) else 0)
        cl = getattr(c, 'close', 0) or (c.get('close', 0) if isinstance(c, dict) else 0)
        v = getattr(c, 'volume', 0) or (c.get('volume', 0) if isinstance(c, dict) else 0)
        points.append(DataPoint(
            timestamp=float(ts), symbol=symbol, timeframe=timeframe,
            source=source, open=float(o), high=float(h),
            low=float(l), close=float(cl), volume=float(v),
        ))
    provenance = DataProvenance(
        original_source=source,
        ingest_time=time.time(),
    )
    return DataStream(
        symbol=symbol, timeframe=timeframe, source=source,
        points=points, provenance=provenance,
    )
