import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom201", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom201"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT


class _NullLogger:
    def debug(self, *a): pass
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass
    def critical(self, *a): pass


class FakeEventBus:
    def __init__(self):
        self.published = []
        self._handlers = {}

    def subscribe(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=201, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


CFG = {"lookback": 2}  # window = 5


async def _run(bars, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    for i, (h, l) in enumerate(bars):
        c = (h + l) / 2
        await atom._on_candle({"symbol": "NQ100", "open": c, "high": h, "low": l,
                               "close": c, "volume": 1, "timeframe": "60s",
                               "period_start": float(i), "timestamp": float(i)})
    swings = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, swings


async def test_warmup_insufficient():
    print("\n--- test_warmup_insufficient ---")
    _atom, _bus, swings = await _run([(10, 9), (11, 10), (12, 11)])  # < window(5)
    assert swings, "لازم ينشر حتى وقت الإحماء"
    last = swings[-1]
    assert last["status"] == "insufficient_data", last["status"]
    assert last["signal"] == "none"
    assert "insufficient_candles" in last["warnings"]
    print("OK — الإحماء: insufficient_data + none")


async def test_detect_swing_high():
    print("\n--- test_detect_swing_high ---")
    # center (index 2) high=15 towers over its 2 neighbours each side
    bars = [(10, 9), (11, 10), (15, 14), (11, 10), (10, 9)]
    _atom, _bus, swings = await _run(bars)
    last = swings[-1]
    assert last["status"] == "ok", last
    assert last["signal"] == "swing_high", last["signal"]
    assert last["metadata"]["price"] == 15, last["metadata"]
    assert last["metadata"]["swing_time"] == 2.0, last["metadata"]["swing_time"]
    assert last["confidence"] == 1.0
    assert last["score"] > 0
    print(f"OK — قمة: price={last['metadata']['price']} "
          f"time={last['metadata']['swing_time']} score={last['score']}")


async def test_detect_swing_low():
    print("\n--- test_detect_swing_low ---")
    # center (index 2) low=3 dips below its 2 neighbours each side
    bars = [(10, 9), (11, 8), (9, 3), (11, 8), (10, 9)]
    _atom, _bus, swings = await _run(bars)
    last = swings[-1]
    assert last["signal"] == "swing_low", last["signal"]
    assert last["metadata"]["price"] == 3, last["metadata"]
    assert last["metadata"]["swing_time"] == 2.0
    print(f"OK — قاع: price={last['metadata']['price']} score={last['score']}")


async def test_monotonic_no_swing():
    print("\n--- test_monotonic_no_swing ---")
    bars = [(10 + i, 9 + i) for i in range(7)]  # steadily rising → no center peak/trough
    _atom, _bus, swings = await _run(bars)
    kinds = {s["signal"] for s in swings}
    assert "swing_high" not in kinds and "swing_low" not in kinds, kinds
    print("OK — صعود رتيب: صفر قمم/قيعان (بحقّ)")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    bars = [(10, 9), (11, 10), (15, 14), (11, 10), (10, 9)]
    _atom, _bus, swings = await _run(bars)
    last = swings[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص بالعقد: {field}"
    for field in ("method", "timeframe", "lookback", "close", "price",
                  "swing_time", "prominence"):
        assert field in last["metadata"], f"حقل metadata ناقص: {field}"
    assert last["id"] == "swing"
    assert last["metadata"]["method"] == "fractal_center"
    assert 0.0 <= last["confidence"] <= 1.0
    print("OK — العقد الموحّد كامل الحقول")


async def test_rejected_candle_counter_tracks_malformed_input():
    print("\n--- test_rejected_candle_counter_tracks_malformed_input ---")
    # بند 26 (فحص من الصفر، متابعة): حدّ خارجيّ حقيقيّ (تغذية شموع) بلا أي
    # عدّاد سابق للمرفوض -- شمعة معطوبة كانت تُرمى بصمت تام.
    bus = FakeEventBus(); atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG))); await atom.start()
    bad = [
        {"symbol": None, "high": 10, "low": 9, "close": 9.5},           # لا رمز
        {"symbol": "NQ100", "high": None, "low": 9, "close": 9.5},      # قمة غائبة
        {"symbol": "NQ100", "high": 10, "low": None, "close": 9.5},     # قاع غائب
        {"symbol": "NQ100", "high": 10, "low": 9, "close": "oops"},     # إغلاق غير رقميّ
        {"symbol": "NQ100", "high": "bad", "low": 9, "close": 9.5},     # قمة نصّية
    ]
    for b in bad:
        await atom._on_candle(b)
    assert atom._rejected_candles == 5, atom._rejected_candles
    assert atom._rejected_by_symbol.get("UNKNOWN") == 1, atom._rejected_by_symbol
    assert atom._rejected_by_symbol.get("NQ100") == 4, atom._rejected_by_symbol
    assert atom._candles_seen == 0, "لا شمعة صالحة وصلت بعد"
    # شمعة سليمة بعدها تُقبل عاديًّا -- الرفض لا يفسد المسار السليم
    await atom._on_candle({"symbol": "NQ100", "high": 10, "low": 9, "close": 9.5,
                           "timeframe": "60s", "period_start": 0.0})
    assert atom._candles_seen == 1
    print(f"OK — 5 شموع معطوبة مرفوضة ومعزولة بالرمز، شمعة سليمة بعدها تعمل عاديًّا")


async def test_rejected_by_symbol_isolates_broken_feed_across_multiple_symbols():
    print("\n--- test_rejected_by_symbol_isolates_broken_feed_across_multiple_symbols ---")
    # الاختبار الجوهريّ: عملات متعدّدة سليمة، عملة واحدة معطوبة الخط --
    # يجب ألّا يختفي عطبها خلف صحّة البقيّة، ويجب ألّا تتأثّر البقيّة به.
    bus = FakeEventBus(); atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG))); await atom.start()
    healthy_symbols = ["NQ100", "BTCUSD", "EURUSD"]
    broken_symbol = "XAUUSD"

    async def feed_good_swing_high(symbol):
        bars = [(10, 9), (11, 10), (15, 14), (11, 10), (10, 9)]
        for i, (h, l) in enumerate(bars):
            c = (h + l) / 2
            await atom._on_candle({"symbol": symbol, "high": h, "low": l, "close": c,
                                   "timeframe": "60s", "period_start": float(i)})

    for sym in healthy_symbols:
        await feed_good_swing_high(sym)
    for _ in range(12):
        await atom._on_candle({"symbol": broken_symbol, "high": None, "low": 9, "close": 9.5})

    health = await atom.health_check()
    assert health.state == HealthState.HEALTHY, (
        "عملات سليمة تعمل -- الصحّة العامّة يجب أن تبقى HEALTHY رغم عطب عملة واحدة")
    assert health.details["rejected"] == 12, health.details
    assert health.details["rejected_by_symbol"] == {broken_symbol: 12}, (
        "عطب XAUUSD يجب أن يظهر باسمها تحديدًا -- لا يضيع بين البقيّة ولا يُنسَب لغيرها")

    swings = [p for n, p in bus.published if n == EVENT_OUT]
    for sym in healthy_symbols:
        sym_swings = [s for s in swings if s["symbol"] == sym]
        assert any(s["signal"] == "swing_high" for s in sym_swings), (
            f"{sym} كانت سليمة ويجب أن تُنتج قمّتها بلا تأثّر بعطب {broken_symbol}")
    assert not [s for s in swings if s["symbol"] == broken_symbol and s["signal"] != "none"], (
        f"{broken_symbol} لم تصل شمعة سليمة واحدة لها -- لا يجوز أن تُنتج إشارة حقيقية")
    print(f"OK — {len(healthy_symbols)} عملات سليمة أنتجت قممها بلا تأثّر، "
          f"و{broken_symbol} المعطوبة مُعزَّاة باسمها بدقّة (12/12) لا مخفيّة خلف صحّة البقيّة")


async def test_all_rejected_health_message_distinct_from_no_input_yet():
    print("\n--- test_all_rejected_health_message_distinct_from_no_input_yet ---")
    bus1 = FakeEventBus(); a1 = Atom()
    await a1.initialize(bus1.make_context(dict(CFG))); await a1.start()
    h_truly_empty = await a1.health_check()
    assert h_truly_empty.message == "NO_CANDLES_YET", h_truly_empty.message

    bus2 = FakeEventBus(); a2 = Atom()
    await a2.initialize(bus2.make_context(dict(CFG))); await a2.start()
    await a2._on_candle({"symbol": "NQ100", "high": None, "low": 9, "close": 9.5})
    h_all_rejected = await a2.health_check()
    assert h_all_rejected.message == "ALL_CANDLES_REJECTED_SO_FAR", h_all_rejected.message
    assert h_all_rejected.state == HealthState.DEGRADED, h_all_rejected.state
    print("OK — 'لا مدخل بعد' و'كل المدخل مرفوض' رسالتان مختلفتان، لا خلط بين حالتين مختلفتين فعليًّا")


async def test_rejected_by_symbol_bounded_but_total_stays_accurate():
    print("\n--- test_rejected_by_symbol_bounded_but_total_stays_accurate ---")
    # نفس عائلة حدّ 552 (_MAX_TRACKED): رمز مرفوض جديد باستمرار (تغذية
    # مشوَّهة أو عبثيّة) يجب ألّا يُنمّي القاموس بلا حدّ -- لكن المجموع
    # الكلّي (عدد صحيح بسيط) يبقى دقيقًا دومًا مهما تعدّدت الأسماء.
    bus = FakeEventBus(); atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG))); await atom.start()
    n_symbols = 200
    for i in range(n_symbols):
        await atom._on_candle({"symbol": f"FAKE{i}", "high": None, "low": 9, "close": 9.5})
    assert atom._rejected_candles == n_symbols, atom._rejected_candles
    assert len(atom._rejected_by_symbol) == 64, (
        f"يجب أن يبقى القاموس محدودًا بـ64، والفعليّ {len(atom._rejected_by_symbol)}")
    print(f"OK — {n_symbols} رمزًا مرفوضًا مختلفًا: المجموع دقيق ({atom._rejected_candles})، "
          f"والقاموس محدود (64) لا ينمو بلا سقف")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY, "قبل start"
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED, "بعد start بلا شموع"
    await atom._on_candle({"symbol": "NQ100", "open": 10, "high": 10, "low": 9,
                           "close": 10, "volume": 1, "timeframe": "60s",
                           "period_start": 0.0, "timestamp": 0.0})
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY, "بعد وصول شمعة"
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [
        test_warmup_insufficient,
        test_detect_swing_high,
        test_detect_swing_low,
        test_monotonic_no_swing,
        test_contract_shape_complete,
        test_rejected_candle_counter_tracks_malformed_input,
        test_rejected_by_symbol_isolates_broken_feed_across_multiple_symbols,
        test_all_rejected_health_message_distinct_from_no_input_yet,
        test_rejected_by_symbol_bounded_but_total_stays_accurate,
        test_health_states,
    ]
    failed = []
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"ERROR: {t.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
