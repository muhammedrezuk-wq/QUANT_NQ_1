# -*- coding: utf-8 -*-
"""
أداة قياس المرحلة 0 — زمن انتشار التكة الصالحة حتى مخرج كل قسم (100 ← 550)
=====================================================================
المشروع:      QUANT_NQ
الورقة:       «ورقة تفاصيل أداة قياس المرحلة 0» (تقارير_التدقيق/) — إصدار 1.0
الإذن:        موافقة المالك 2026-08-22 على بناء هذه الأداة في tools/ فقط، لا أكثر.
الحالة:       أداة مؤقتة — تُشغَّل للقياس ثم تُخرَج من الحزمة النهائية.

المبدأ: «تسمع فقط» — مراقب لا يشارك.
--------------------------------------------------
هذه الأداة مستمع سلبي بالكامل على ناقل الأحداث الحيّ:

  * تسجّل في market.tick.validated وفي أحداث مخرجات الأقسام (الأسماء
    مأخوذة من manifest كل قسم فعليًا — لا اسم مفترض واحد).
  * تقيس زمن الانتشار: من لحظة وصول التكة الصالحة حتى مخرج كل قسم،
    وتشمل انتظار الطوابير (وهو ما يهمّ التداول فعلًا).
  * تنتج جدولًا: count / mean / p50 / p95 / p99 / p99.9 / max / jitter.

الضمانات الصريحة (من الورقة §5 — كلها مطبَّقة في هذا الملف):
  ✗ لا تلمس النواة المجمّدة core/ — لا تستورد منها شيئًا في وضع القياس.
  ✗ لا تعدّل أي atom.py ولا manifest.yaml.
  ✗ لا تضيف أي انتظار للمسار السريع: معالجاتها متزامنة (sync) O(1) فقط.
  ✗ لا ذكاء، لا اقتراح معايرة، لا تغيير أوزان أو معاملات.
  ✗ لا تكتب في قاعدة البيانات أصلًا (الكتابة اختيارية لتقرير نصي عبر ‎--out).
  ✗ لا ترسل أوامر، لا تنشر أي حدث، لا توقف أي شيء.
  ✓ ذاكرة محدودة السقف (deque بحدّ النافذة + سجل تكات قيد الطيران محدود).

طريقة الربط بالطابعين:
  1) «استلام→استلام» perf_counter: الأدقّ — تعمل إذا رُبطت الأداة قبل بدء
     الذرّات (نمط أدوات الحوكمة عند الإقلاع).
  2) «نشر→نشر» بطوابع الناقل (timestamp التي يحقنها الناقل عند النشر):
     احتياطي يصحّح القياس حتى لو رُبطت الأداة والنظام يعمل (مخرجات وصلت
     قبل تسجيل تكتّها تُقاس بطوابع النشر عند وصولها ضمن نافذة الانتظار).

الاستخدام:
  python tools/phase0_latency_probe.py --help
  python tools/phase0_latency_probe.py --selftest          # فحص ذاتي: محاكاة على ناقل حقيقي
  (للقياس الحيّ: تُربَط داخل عملية النظام عند الإقلاع — attach(bus) — قبل بدء الذرّات)

ما لا تقيسه (صدق تقني — من الورقة §4): لا تقيس الزمن داخل المعالج نفسه
(يتطلب تعديل النواة = ممنوع)، ولا تحكم على «سبب» البطء — تعطي الأرقام فقط.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# عقود الأحداث — مأخوذة حرفيًا من manifest كل قسم (تحقُّق 2026-08-22)
# --------------------------------------------------------------------------
TICK_EVENT = "market.tick.validated"

# (رقم القسم، التسمية العربية، حدث المخرج، ملاحظة)
SECTION_EVENTS: tuple[tuple[int, str, str, str], ...] = (
    (150, "التحليل",          "analysis.cycle.collected",       ""),
    (200, "البنية",           "structure.cycle.collected",      ""),
    (250, "السيولة",          "liquidity.cycle.collected",      ""),
    (300, "الإحصاء",          "stats.cycle.collected",          ""),
    (350, "الاحتمالات",       "probability.cycle.collected",    ""),
    (400, "الاستراتيجيات",    "strategy.cycle.collected",       ""),
    (450, "القرار",           "decision.cycle.collected",       ""),
    (451, "تجميع القرار",     "decision.aggregated.state",      ""),
    (455, "بوابة الشراء",     "decision.eligibility.buy.state", "يصدر عند وجود إشارة فقط"),
    (456, "بوابة البيع",      "decision.eligibility.sell.state", "يصدر عند وجود إشارة فقط"),
    (468, "التحكم بالأصول",   "decision.filter.asset.state",    "لا مخرجًا لكل تكة — يُسجَّل ما يصدر فقط"),
    (500, "المخاطر",          "risk.unified.state",             ""),
    (513, "تحجيم المركز",     "risk.position_size.state",       ""),
    (550, "التنفيذ",          "execution.unified.state",        "نهاية المسار"),
)

END_TO_END_SECTION = 550  # «تكة → 550» = صف END-TO-END في التقرير

IN_FLIGHT_CAP = 8_192     # سقف التكات قيد الطيران (ذاكرة محدودة)
PENDING_OUT_CAP = 4_096   # سقف مخرجات وصلت قبل تكتّها (ربط متأخر)


def _pct(sorted_samples: list[float], p: float) -> float:
    """مرجع الرتبة الأقرب (nearest-rank) — متحفّظ ولا يخترع قيمًا بينية."""
    if not sorted_samples:
        return float("nan")
    idx = max(0, math.ceil((p / 100.0) * len(sorted_samples)) - 1)
    return sorted_samples[min(idx, len(sorted_samples) - 1)]


class Phase0LatencyProbe:
    """مستمع سلبي: يقيس زمن انتشار التكة الصالحة حتى مخرج كل قسم.

    لا ينشر أبدًا، ولا يكتب شيئًا، ومعالجاته متزامنة O(1) حتى لا يضيف
    أي كلفة تُذكر على المسار السريع.
    """

    def __init__(self, *, max_ticks: int = 5_000, max_seconds: float = 600.0) -> None:
        self.max_ticks = max(1, int(max_ticks))
        self.max_seconds = float(max_seconds)

        self._ticks_seen = 0
        self._started_wall: float | None = None
        self._ended_wall: float | None = None
        self._started_mono = time.perf_counter()

        # trace_id → (t_receipt_mono, t_bus_publish)
        self._in_flight: "OrderedDict[str, tuple[float, float]]" = OrderedDict()
        # trace_id → {section_id: t_bus_publish} — مخرجات وصلت قبل تكتّها
        self._pending_out: "OrderedDict[str, dict[int, float]]" = OrderedDict()
        self._evicted_ticks = 0

        # قسم → عيّنات (بالثانية) بقائمة محدودة بحدّ النافذة
        self._samples: dict[int, deque[float]] = {
            sec[0]: deque(maxlen=self.max_ticks) for sec in SECTION_EVENTS
        }
        self._method_receipt: dict[int, int] = {sec[0]: 0 for sec in SECTION_EVENTS}
        self._method_bus_ts: dict[int, int] = {sec[0]: 0 for sec in SECTION_EVENTS}
        self._unmatched: dict[int, int] = {sec[0]: 0 for sec in SECTION_EVENTS}
        self._pending_now: dict[int, int] = {sec[0]: 0 for sec in SECTION_EVENTS}

        self._subs: list[tuple[str, Callable[[dict], None]]] = []
        self._attached = False

    # -- ربط / فصل ----------------------------------------------------------

    def attach(self, bus: Any) -> None:
        """الاشتراك في التكة + مخرجات الأقسام. يُفضَّل قبل بدء الذرّات."""
        if self._attached:
            return
        self._started_wall = time.time()
        self._started_mono = time.perf_counter()

        def on_tick(payload: dict) -> None:
            self._on_tick(payload)

        bus.subscribe(TICK_EVENT, on_tick, subscriber="phase0_probe")
        self._subs.append((TICK_EVENT, on_tick))

        for sec_id, _label, event, _note in SECTION_EVENTS:
            handler = self._make_section_handler(sec_id)
            bus.subscribe(event, handler, subscriber="phase0_probe")
            self._subs.append((event, handler))

        self._attached = True

    def detach(self, bus: Any) -> None:
        """فكّ كل اشتراكات الأداة (خروج نظيف بلا أثر)."""
        for event, handler in self._subs:
            try:
                bus.unsubscribe(event, handler)
            except Exception:
                pass
        self._subs.clear()
        self._attached = False
        self._ended_wall = time.time()

    # -- المعالجات (متزامنة، O(1)، لا تنشر ولا تكتب) ------------------------

    def _on_tick(self, payload: dict) -> None:
        trace_id = payload.get("trace_id")
        if not trace_id:
            return  # تكة بلا أثر — لا يمكن ربطها بأمان؛ تُتجاهل وتُذكر في التقرير
        self._ticks_seen += 1
        t_now = time.perf_counter()
        t_bus = payload.get("timestamp")
        self._in_flight[trace_id] = (t_now, float(t_bus) if isinstance(t_bus, (int, float)) else t_now)
        while len(self._in_flight) > IN_FLIGHT_CAP:
            self._in_flight.popitem(last=False)
            self._evicted_ticks += 1

        # مخرجات وصلت قبل تكتّها (ربط متأخر) → تُقاس بطوابع النشر نشر→نشر
        pending = self._pending_out.pop(trace_id, None)
        if pending:
            tick_bus = self._in_flight[trace_id][1]
            for sec_id, out_bus in pending.items():
                delta = out_bus - tick_bus
                if delta >= 0:
                    self._samples[sec_id].append(delta)
                    self._method_bus_ts[sec_id] += 1
                self._pending_now[sec_id] -= 1

    def _make_section_handler(self, sec_id: int) -> Callable[[dict], None]:
        def on_section_output(payload: dict) -> None:
            trace_id = payload.get("trace_id")
            if not trace_id:
                self._unmatched[sec_id] += 1
                return
            # حدث جذر (بلا أب) ليس مخرج تكة أصلًا — الناقل يجعل كل مخرج حقيقي
            # ابنًا في شجرة أثر التكة، فما لا أب له لا تكة له: يُصنَّف فورًا.
            if payload.get("parent_event_id") is None and payload.get("parent_event") is None:
                self._unmatched[sec_id] += 1
                return
            entry = self._in_flight.get(trace_id)
            if entry is not None:
                delta = time.perf_counter() - entry[0]
                if delta >= 0:
                    self._samples[sec_id].append(delta)
                    self._method_receipt[sec_id] += 1
                return
            # وصلت قبل تكتّها؟ (الأداة رُبطت بعد الذرّات) — ننتظر تكتّها قليلًا
            t_bus = payload.get("timestamp")
            if isinstance(t_bus, (int, float)):
                bucket = self._pending_out.setdefault(trace_id, {})
                if sec_id not in bucket:
                    self._pending_now[sec_id] += 1
                bucket[sec_id] = float(t_bus)
                while len(self._pending_out) > PENDING_OUT_CAP:
                    _tid, dropped = self._pending_out.popitem(last=False)
                    for dropped_sec in dropped:
                        self._pending_now[dropped_sec] -= 1
                        self._unmatched[dropped_sec] += 1
            else:
                self._unmatched[sec_id] += 1

        return on_section_output

    # -- نافذة القياس --------------------------------------------------------

    @property
    def ticks_seen(self) -> int:
        return self._ticks_seen

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self._started_mono

    def done(self) -> bool:
        if self._ticks_seen >= self.max_ticks:
            return True
        if self._ticks_seen > 0 and self.elapsed_seconds >= self.max_seconds:
            return True
        return False


async def run_until_done(probe: Phase0LatencyProbe, *, poll_s: float = 0.25) -> None:
    """انتظار سلبي حتى اكتمال النافذة (عدد تكات أو مدة) — بلا أي تأثير على النظام."""
    while not probe.done():
        await asyncio.sleep(poll_s)
    probe._ended_wall = probe._ended_wall or time.time()


# --------------------------------------------------------------------------
# الإحصاء والتقرير
# --------------------------------------------------------------------------

def _stats(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {k: float("nan") for k in
                ("count", "mean", "p50", "p95", "p99", "p999", "max", "jitter")}
    ordered = sorted(samples)
    n = len(ordered)
    mean = sum(ordered) / n
    p50, p95, p99, p999 = (_pct(ordered, p) for p in (50, 95, 99, 99.9))
    return {
        "count": float(n),
        "mean": mean,
        "p50": p50,
        "p95": p95,
        "p99": p99,
        "p999": p999,
        "max": ordered[-1],
        "jitter": p99 - p50,
    }


def _fmt_ms(value: float) -> str:
    if math.isnan(value):
        return "—"
    v = value * 1000.0
    return f"{v:.1f}" if v < 100 else f"{v:.0f}"


def report_lines(probe: Phase0LatencyProbe) -> list[str]:
    probe._ended_wall = probe._ended_wall or time.time()
    duration = probe.elapsed_seconds
    lines: list[str] = []
    lines.append("سجلّ قياس زمن الأقسام — المرحلة 0 — QUANT_NQ")
    lines.append("=" * 78)
    lines.append(
        f"النافذة المطلوبة: {probe.max_ticks} تكة صالحة أو {probe.max_seconds:.0f} ثانية — "
        f"تحقّق: {probe.ticks_seen} تكة خلال {duration:.1f} ثانية"
    )
    lines.append(
        "القياس: زمن الانتشار من استلام التكة الصالحة (market.tick.validated) "
        "حتى استلام مخرج القسم — يشمل انتظار الطوابير."
    )
    lines.append("")
    header = f"{'القسم':<22}{'count':>7}{'mean':>9}{'p50':>9}{'p95':>9}{'p99':>9}{'p99.9':>9}{'max':>9}{'jitter':>9}"
    lines.append(header)
    lines.append("-" * len(header))

    for sec_id, label, _event, _note in SECTION_EVENTS:
        n = len(probe._samples[sec_id])
        st = _stats(list(probe._samples[sec_id]))
        name = f"{sec_id} {label}"
        lines.append(
            f"{name:<22}{n:>7}{_fmt_ms(st['mean']):>9}{_fmt_ms(st['p50']):>9}"
            f"{_fmt_ms(st['p95']):>9}{_fmt_ms(st['p99']):>9}{_fmt_ms(st['p999']):>9}"
            f"{_fmt_ms(st['max']):>9}{_fmt_ms(st['jitter']):>9}"
        )

    e2e_samples = list(probe._samples[END_TO_END_SECTION])
    e2e = _stats(e2e_samples)
    lines.append("-" * len(header))
    lines.append(
        f"{'END-TO-END (تكة → 550)':<22}{len(e2e_samples):>7}{_fmt_ms(e2e['mean']):>9}"
        f"{_fmt_ms(e2e['p50']):>9}{_fmt_ms(e2e['p95']):>9}{_fmt_ms(e2e['p99']):>9}"
        f"{_fmt_ms(e2e['p999']):>9}{_fmt_ms(e2e['max']):>9}{_fmt_ms(e2e['jitter']):>9}"
    )
    lines.append("(كل الأزمنة بالمللي ثانية — jitter = p99 − p50)")

    # صدق القياس: مطابقة/عدم مطابقة وطريقة القياس لكل قسم
    lines.append("")
    lines.append("الصدق التقني — تغطية الربط لكل قسم (matched/unmatched):")
    for sec_id, label, _event, note in SECTION_EVENTS:
        matched = len(probe._samples[sec_id])
        unm = probe._unmatched[sec_id]
        pend = probe._pending_now[sec_id]
        methods = []
        if probe._method_receipt[sec_id]:
            methods.append(f"استلام×{probe._method_receipt[sec_id]}")
        if probe._method_bus_ts[sec_id]:
            methods.append(f"طوابع×{probe._method_bus_ts[sec_id]}")
        method_txt = " + ".join(methods) if methods else "—"
        note_txt = f"  ({note})" if note else ""
        pend_txt = f" / {pend} بانتظار تكتّها" if pend else ""
        lines.append(f"  {sec_id} {label}: {matched} مطابق / {unm} بلا تكة{pend_txt}  [{method_txt}]{note_txt}")
    if probe._evicted_ticks:
        lines.append(f"  تكات أُخرجت من سجل الطيران قبل اكتمال مخرجاتها: {probe._evicted_ticks}")
    lines.append("")
    lines.append("ملاحظات:")
    lines.append("- أحداث إعادة التشغيل الأخيرة (replay عند الاشتراك) تظهر مرة بلا تكة = غير محسوبة.")
    lines.append("- لا يقيس الزمن داخل المعالج نفسه ولا يحكم على سبب البطء (الورقة §4).")
    lines.append("- الأداة لم تكتب في قاعدة بيانات، لم تنشر حدثًا، ولم تغيّر معاملًا.")
    return lines


def to_dict(probe: Phase0LatencyProbe) -> dict[str, Any]:
    return {
        "tool": "phase0_latency_probe",
        "version": __version__,
        "tick_event": TICK_EVENT,
        "window": {"max_ticks": probe.max_ticks, "max_seconds": probe.max_seconds,
                   "ticks_seen": probe.ticks_seen, "elapsed_s": probe.elapsed_seconds},
        "sections": {
            str(sec_id): {"label": label, "event": event, **_stats(list(probe._samples[sec_id])),
                          "unmatched": probe._unmatched[sec_id],
                          "method_receipt": probe._method_receipt[sec_id],
                          "method_bus_ts": probe._method_bus_ts[sec_id]}
            for sec_id, label, event, _note in SECTION_EVENTS
        },
    }


# --------------------------------------------------------------------------
# الفحص الذاتي — محاكاة على ناقل النواة الحقيقي (لا ذرّات، لا كتابة، لا شبكة)
# --------------------------------------------------------------------------

async def _selftest(ticks: int = 300) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.event_bus import EventBus  # استيراد قراءة فقط — لا تعديل نواة

    bus = EventBus()
    probe = Phase0LatencyProbe(max_ticks=ticks, max_seconds=120.0)
    probe.attach(bus)  # الأداة أولًا — النمط الموصى به عند الإقلاع الحيّ

    # خط أنابيب وهمي داخل شجرة أثر التكة (يرث trace_id تلقائيًا عبر الناقل)
    async def pipeline(payload: dict) -> None:
        await asyncio.sleep(0.0006)
        await bus.publish("analysis.cycle.collected", {"symbol": "NQ"})
        await asyncio.sleep(0.0004)
        await bus.publish("structure.cycle.collected", {"symbol": "NQ"})
        await bus.publish("liquidity.cycle.collected", {"symbol": "NQ"})
        await asyncio.sleep(0.0005)
        await bus.publish("stats.cycle.collected", {"symbol": "NQ"})
        await bus.publish("probability.cycle.collected", {"symbol": "NQ"})
        await asyncio.sleep(0.0007)
        await bus.publish("strategy.cycle.collected", {"symbol": "NQ"})
        await bus.publish("decision.cycle.collected", {"symbol": "NQ"})
        await asyncio.sleep(0.0003)
        await bus.publish("decision.aggregated.state", {"symbol": "NQ"})
        n = payload.get("seq", 0)
        if n % 3 == 0:  # البوابات لا تصدر مع كل تكة — عمدًا
            await bus.publish("decision.eligibility.buy.state", {"symbol": "NQ"})
        if n % 5 == 0:
            await bus.publish("decision.eligibility.sell.state", {"symbol": "NQ"})
        await asyncio.sleep(0.0008)
        await bus.publish("risk.unified.state", {"symbol": "NQ"})
        await bus.publish("risk.position_size.state", {"symbol": "NQ"})
        await asyncio.sleep(0.0002)
        await bus.publish("execution.unified.state", {"symbol": "NQ"})

    bus.subscribe(TICK_EVENT, pipeline, subscriber="selftest_pipeline")

    async def orphan_generator() -> None:
        # أحداث بلا تكة (من مهمة حرّة بلا سياق أثر) — يجب أن تُحسب unmatched
        for _ in range(7):
            await asyncio.sleep(0.01)
            await bus.publish("analysis.cycle.collected", {"symbol": "NQ"})

    orphan_task = asyncio.create_task(orphan_generator())

    for seq in range(ticks):
        await bus.publish(TICK_EVENT, {"symbol": "NQ", "seq": seq}, publisher="selftest_feed")
        await asyncio.sleep(0.001)

    await run_until_done(probe)
    await orphan_task
    probe.detach(bus)

    print("\n".join(report_lines(probe)))

    ok = True
    required = {s[0] for s in SECTION_EVENTS} - {468}
    for sec_id in required:
        if not probe._samples[sec_id]:
            print(f"✗ فشل الفحص: القسم {sec_id} لم يسجّل أي عيّنة")
            ok = False
    if probe._unmatched[150] < 7:
        print("✗ فشل الفحص: الأحداث اليتيمة لم تُحسب unmatched كما يجب")
        ok = False
    if probe._method_receipt[550] == 0:
        print("✗ فشل الفحص: مسار النهاية-إلى-النهاية (550) بلا عيّنات")
        ok = False
    print("\nنتيجة الفحص الذاتي:", "نجح ✅" if ok else "فشل ❌")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# واجهة التشغيل
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="أداة قياس المرحلة 0 — زمن انتشار التكة حتى مخرج كل قسم (مراقب فقط)")
    parser.add_argument("--ticks", type=int, default=5_000,
                        help="حدّ نافذة القياس بعدد التكات (افتراضي 5000)")
    parser.add_argument("--seconds", type=float, default=600.0,
                        help="حدّ نافذة القياس بالثواني — أيّهما تحقّق أولًا (افتراضي 600)")
    parser.add_argument("--selftest", action="store_true",
                        help="فحص ذاتي: محاكاة كاملة على ناقل النواة الحقيقي ثم خروج")
    parser.add_argument("--out", type=str, default="",
                        help="مسار اختياري لتصدير النتائج JSON (لا كتابة افتراضيًا)")
    args = parser.parse_args(argv)

    if args.selftest:
        return asyncio.run(_selftest())

    # وضع القياس الحيّ يُحمَّل داخل عملية النظام (attach(bus)) — لا يُشغَّل
    # مستقلًا لأن الناقل يعيش مع النظام. نطبع التعليمات ونخرج بأمان.
    print(__doc__)
    print("التشغيل الحيّ: أضِف داخل عملية النظام عند الإقلاع (قبل بدء الذرّات):")
    print("    from tools.phase0_latency_probe import Phase0LatencyProbe, run_until_done")
    print("    probe = Phase0LatencyProbe(max_ticks=%d, max_seconds=%.0f)" % (args.ticks, args.seconds))
    print("    probe.attach(bus)")
    print("    await run_until_done(probe)   # ثم اطبع التقرير وافصل الأداة")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
