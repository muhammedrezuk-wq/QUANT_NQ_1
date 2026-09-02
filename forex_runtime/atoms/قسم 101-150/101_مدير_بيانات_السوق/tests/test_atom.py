import asyncio
import inspect
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext  # noqa: E402
import importlib.util as _ilu  # noqa: E402
import sys as _sys  # noqa: E402
from pathlib import Path as _AtomPath  # noqa: E402
_MOD_NAME = "_atom101"
_spec = _ilu.spec_from_file_location(
    _MOD_NAME, _AtomPath(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
_sys.modules[_MOD_NAME] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

_UNIFIED = "market_data.state"


class _NullLogger:
    def debug(self, *a): pass
    def info(self, *a): pass
    def warning(self, *a): pass
    def error(self, *a): pass
    def critical(self, *a): pass


class FakeEventBus:
    def __init__(self):
        self.published = []
        self._handlers: dict[str, list] = {}

    def subscribe(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))
        for handler in self._handlers.get(name, []):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def make_context(self):
        return AtomContext(atom_id=101, config={}, logger=_NullLogger(), publish=self.publish, subscribe=self.subscribe)


async def test_unified_state_accumulates_across_event_types():
    print("\n--- test_unified_state_accumulates_across_event_types ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()

    await bus.publish("market_data.price_received", {"symbol": "NQ", "bid": 100.0, "ask": 100.5})
    await bus.publish("market_data.volume_received", {"symbol": "NQ", "volume": 1000})
    await bus.publish("market_data.spread_updated", {"symbol": "NQ", "spread": 0.5})

    unified_events = [p for n, p in bus.published if n == _UNIFIED]
    assert len(unified_events) == 3, "لقطة تُنشَر عند كل تحديث"
    last_state = unified_events[-1]["state"]
    assert set(last_state.keys()) == {"price", "volume", "spread"}, "اللقطة الأخيرة تحوي كل الحقول المتراكمة، لا آخر واحد بس"
    print(f"OK — اللقطة الأخيرة تراكمت من 3 أنواع مختلفة: {list(last_state.keys())}")


async def test_publishes_state_suffix_event():
    """قاعدة 16 — اسم الحدث المنشور لازم يكون بلاحقة حالة (قابل للإعادة)."""
    print("\n--- test_publishes_state_suffix_event ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    await bus.publish("market_data.price_received", {"symbol": "NQ", "bid": 1})
    names = {n for n, _ in bus.published}
    assert _UNIFIED in names, f"لازم ينشر {_UNIFIED}"
    assert _UNIFIED.endswith(".state"), "الحدث لازم يكون بلاحقة حالة (قاعدة 16)"
    print(f"OK — نشر حدث حالة قابل للإعادة: {_UNIFIED}")


async def test_nine_event_types_no_late_binding_bug():
    """أهم اختبار — يثبت عدم وجود عيب الإغلاق المتأخر عبر التسعة أنواع."""
    print("\n--- test_nine_event_types_no_late_binding_bug ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()

    event_names = [
        "market_data.price_received", "market_data.candle_closed", "market_data.volume_received",
        "market_data.spread_updated", "market_data.depth_updated", "market_data.trade_tape_updated",
        "market_data.news_received", "market_data.calendar_event", "market_data.reference_index_updated",
    ]
    for name in event_names:
        await bus.publish(name, {"symbol": "NQ"})

    final_state = atom._state["NQ"]
    expected_keys = {
        "price", "candle", "volume", "spread", "depth", "trade_tape", "news", "calendar", "reference_index",
    }
    assert set(final_state.keys()) == expected_keys, (
        f"كل نوع حدث لازم يخزَّن بمفتاحه الصحيح بالضبط، لا كلهم بآخر مفتاح (عيب إغلاق متأخر)، لقيت: {set(final_state.keys())}"
    )
    print(f"OK — التسعة أنواع خُزِّنت كل واحد بمفتاحه الصحيح: {sorted(final_state.keys())}")


async def test_symbols_tracked_independently():
    print("\n--- test_symbols_tracked_independently ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    await bus.publish("market_data.price_received", {"symbol": "NQ", "bid": 1})
    await bus.publish("market_data.price_received", {"symbol": "ES", "bid": 2})
    assert set(atom._state.keys()) == {"NQ", "ES"}
    assert atom._state["NQ"]["price"]["bid"] == 1
    assert atom._state["ES"]["price"]["bid"] == 2
    print(f"OK — NQ وES بحالتين مستقلتين تمامًا: {atom._state}")


async def test_missing_symbol_goes_global_without_crash():
    """حالة فشل/حافة (قاعدة 9) — payload بلا symbol ما ينهار، يروح لـ_global."""
    print("\n--- test_missing_symbol_goes_global_without_crash ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context())
    await atom.start()
    await bus.publish("market_data.price_received", {"bid": 1})  # بلا symbol
    assert "_global" in atom._state, "payload بلا symbol لازم يروح لـ_global"
    assert atom._state["_global"]["price"]["bid"] == 1
    print("OK — payload بلا symbol راح لـ_global بلا انهيار")


async def main():
    tests = [
        test_unified_state_accumulates_across_event_types,
        test_publishes_state_suffix_event,
        test_nine_event_types_no_late_binding_bug,
        test_symbols_tracked_independently,
        test_missing_symbol_goes_global_without_crash,
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
