# -*- coding: utf-8 -*-
"""محرك الزمن التاريخي — Historical Clock.

يقدّم البيانات بالترتيب الزمني الصارم.
يمنع أي قراءة مستقبلية.
يعيد بناء الحالة كما كانت وقت كل قرار.

هذا المحرك هو الفرق بين باك تست صادق ونتيجة مزوّرة.
بدونه، أي تحليل قد يرى بيانات لم تكن متاحة وقت القرار.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backtest.data_contract import DataPoint, DataStream


class LookAheadError(Exception):
    """يُرمى عند محاولة قراءة بيانات مستقبلية."""
    pass


@dataclass
class ClockState:
    """حالة الساعة — تُحفظ وتُستعاد."""
    current_time: float = 0.0
    position: int = 0
    total_points: int = 0
    started_at: float = 0.0
    symbol: str = ""
    timeframe: str = ""
    source: str = ""
    look_ahead_violations: int = 0
    points_delivered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_time": self.current_time,
            "position": self.position,
            "total_points": self.total_points,
            "points_delivered": self.points_delivered,
            "look_ahead_violations": self.look_ahead_violations,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }


class HistoricalClock:
    """محرك الزمن التاريخي.

    الاستخدام:
        clock = HistoricalClock(stream)
        for point in clock:
            # هنا point هو كل ما هو متاح حتى هذه اللحظة
            # ممنوع الوصول لـ stream.points[pos+1] من داخل الحلقة
            analyze(point)

    الخصائص:
    - يقدّم نقطة واحدة في كل خطوة
    - يمنع القفز للأمام
    - يسجّل كل مخالفة
    - يمكن إيقافه واستعادته
    - يمكن تشغيله بسرعة (simulation speed)
    """

    def __init__(self, stream: DataStream, strict: bool = True):
        self._stream = stream
        self._strict = strict  # True = ارمِ LookAheadError; False = سجّل فقط
        self._state = ClockState(
            total_points=len(stream.points),
            symbol=stream.symbol,
            timeframe=stream.timeframe,
            source=stream.source,
            started_at=time.time(),
        )
        self._fence: float = 0.0  # آخر وقت مُسلّم — ممنوع تجاوزه
        self._stopped = False
        self._speed: float = 0.0  # 0 = بأسرع ما يمكن; >0 = عامل تأخير

    @property
    def state(self) -> ClockState:
        return self._state

    @property
    def current_time(self) -> float:
        return self._state.current_time

    @property
    def position(self) -> int:
        return self._state.position

    @property
    def remaining(self) -> int:
        return self._state.total_points - self._state.position

    @property
    def violations(self) -> int:
        return self._state.look_ahead_violations

    def __iter__(self):
        return self

    def __next__(self) -> DataPoint:
        if self._stopped:
            raise StopIteration
        if self._state.position >= self._state.total_points:
            raise StopIteration
        point = self._stream.points[self._state.position]
        self._state.current_time = point.timestamp
        self._fence = point.timestamp
        self._state.position += 1
        self._state.points_delivered += 1
        return point

    def advance_to(self, target_time: float) -> list[DataPoint]:
        """تقديم حتى وقت محدد — يرجع كل النقاط حتى ذلك الوقت."""
        delivered: list[DataPoint] = []
        while self._state.position < self._state.total_points:
            point = self._stream.points[self._state.position]
            if point.timestamp > target_time:
                break
            delivered.append(point)
            self._state.current_time = point.timestamp
            self._fence = point.timestamp
            self._state.position += 1
            self._state.points_delivered += 1
        return delivered

    def peek(self, offset: int = 0) -> DataPoint | None:
        """نظر محدود — لكن فقط ضمن النافذة المسموحة.

        ممنوع النظر لأكثر من 0 (النقطة الحالية لم تُسلَّم بعد).
        offset=0: النقطة التالية (لم تُسلَّم)
        offset=-1: آخر نقطة مُسلَّمة
        offset>0: ❌ LOOK-AHEAD — ممنوع
        """
        if offset > 0:
            self._state.look_ahead_violations += 1
            if self._strict:
                raise LookAheadError(
                    f"محاولة قراءة مستقبلية: offset={offset}. "
                    f"الزمن الحالي: {self._state.current_time}. "
                    f"المخالفات: {self._state.look_ahead_violations}"
                )
            return None

        idx = self._state.position + offset
        if 0 <= idx < self._state.total_points:
            return self._stream.points[idx]
        return None

    def visible_window(self, size: int) -> list[DataPoint]:
        """النافذة المرئية — آخر N نقطة بما فيها الحالية.

        هذا ما يراه المحلل فعلياً.
        لا يمكن أن يتجاوز الزمن الحالي.
        """
        start = max(0, self._state.position - size)
        end = self._state.position
        return self._stream.points[start:end]

    def visible_stream(self, size: int) -> DataStream:
        """DataStream مصغّر — النافذة المرئية فقط."""
        window = self.visible_window(size)
        from backtest.data_contract import DataProvenance
        return DataStream(
            symbol=self._stream.symbol,
            timeframe=self._stream.timeframe,
            source=self._stream.source,
            points=list(window),
            provenance=DataProvenance(
                original_source=self._stream.source,
                ingest_time=time.time(),
                transformations=[f"window_{size}_at_{self._state.current_time}"],
            ),
            quality=self._stream.quality,
        )

    def stop(self) -> None:
        """إيقاف الساعة — يمكن استعادتها لاحقاً."""
        self._stopped = True

    def resume(self) -> None:
        """استعادة الساعة — تكمل من حيث توقفت."""
        self._stopped = False

    def save_state(self) -> dict[str, Any]:
        """حفظ الحالة — للاسترداد بعد إعادة تشغيل."""
        return {
            "state": self._state.to_dict(),
            "fence": self._fence,
            "strict": self._strict,
        }

    def restore_state(self, saved: dict[str, Any]) -> None:
        """استعادة الحالة — تكملة من نفس الموقع."""
        s = saved.get("state", {})
        self._state.position = s.get("position", 0)
        self._state.current_time = s.get("current_time", 0.0)
        self._state.points_delivered = s.get("points_delivered", 0)
        self._state.look_ahead_violations = s.get("look_ahead_violations", 0)
        self._fence = saved.get("fence", 0.0)
        self._stopped = False

    def report(self) -> dict[str, Any]:
        """تقرير نهائي — يُرفق مع كل تجربة."""
        return {
            "symbol": self._stream.symbol,
            "timeframe": self._stream.timeframe,
            "source": self._stream.source,
            "total_points": self._state.total_points,
            "points_delivered": self._state.points_delivered,
            "start_time": self._stream.first_ts if self._stream.points else 0,
            "end_time": self._state.current_time,
            "duration_s": self._state.current_time - (self._stream.first_ts if self._stream.points else 0),
            "look_ahead_violations": self._state.look_ahead_violations,
            "violations_free": self._state.look_ahead_violations == 0,
            "completed": self._state.position >= self._state.total_points,
        }
