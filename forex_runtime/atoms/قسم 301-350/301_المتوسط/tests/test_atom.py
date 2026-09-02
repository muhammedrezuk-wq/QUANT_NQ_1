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
    "_atom301", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom301"] = _mod
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
        return AtomContext(atom_id=301, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tick(price, symbol="NQ100", timeframe="tick", sequence=0):
    return {"symbol": symbol, "price": price, "volume": 1, "timeframe": "tick",
            "timestamp": sequence}


async def _run(closes, cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or {"window_size": 20}))
    await atom.start()
    for i, c in enumerate(closes):
        await atom._on_tick(_tick(c, sequence=i))
    out = [p for n, p in bus.published if n == EVENT_OUT]
    return atom, bus, out


async def test_mean_computed():
    print("\n--- test_mean_computed ---")
    _atom, _bus, out = await _run([10, 20, 30])
    last = out[-1]
    assert last["status"] == "ok"
    assert last["metadata"]["value"] == 20, last["metadata"]["value"]
    print(f"OK — متوسط [10,20,30] = {last['metadata']['value']}")


async def test_rolling_window():
    print("\n--- test_rolling_window ---")
    _atom, _bus, out = await _run([10, 20, 30, 40], cfg={"window_size": 3})
    last = out[-1]
    assert last["metadata"]["value"] == 30, last["metadata"]["value"]  # mean of [20,30,40]
    assert last["metadata"]["count"] == 3
    print(f"OK — نافذة متدحرجة(3): متوسط آخر 3 = {last['metadata']['value']}")


async def test_contract_shape_complete():
    print("\n--- test_contract_shape_complete ---")
    _atom, _bus, out = await _run([10, 20, 30])
    last = out[-1]
    for field in ("symbol", "id", "cycle_id", "status", "signal", "score",
                  "confidence", "quality", "warnings", "metadata"):
        assert field in last, f"حقل ناقص: {field}"
    for field in ("method", "timeframe", "window", "count", "value"):
        assert field in last["metadata"], f"حقل metadata ناقص: {field}"
    assert last["id"] == "mean"
    print("OK — العقد الموحّد كامل")


async def test_health_states():
    print("\n--- test_health_states ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"window_size": 20}))
    h0 = await atom.health_check()
    assert h0.state == HealthState.UNHEALTHY
    await atom.start()
    h1 = await atom.health_check()
    assert h1.state == HealthState.DEGRADED
    await atom._on_tick(_tick(10))
    h2 = await atom.health_check()
    assert h2.state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_depth_diverges_from_completeness_when_unstable():
    # §١١ — نافذة ممتلئة بالكامل (data_completeness=100) لكن نصفَيها متباعدان
    # جدًّا (10,10 مقابل 90,90) ⇒ المتوسط لم يستقرّ بعد ⇒ current_depth ناضج
    # منخفض رغم اكتمال البيانات — هذا هو الفصل المطلوب (مثال المالك: 100/35).
    print("\n--- test_depth_diverges_from_completeness_when_unstable ---")
    _atom, _bus, out = await _run([10, 10, 90, 90], cfg={"window_size": 4})
    last = out[-1]
    assert last["data_completeness"] == 100.0, last["data_completeness"]
    assert last["current_depth"] < last["data_completeness"], (
        last["current_depth"], last["data_completeness"])
    print(f"OK — data_completeness=100 لكن current_depth={last['current_depth']} < 100 (تذبذب بين النصفين)")


async def test_depth_matches_completeness_when_stable():
    # §١١ — نافذة ممتلئة ومتماسكة (نصفاها متقاربان) ⇒ current_depth قريب
    # جدًّا من data_completeness=100 (تحليل ناضج فعلًا لا مجرد امتلاء).
    print("\n--- test_depth_matches_completeness_when_stable ---")
    _atom, _bus, out = await _run([100, 101, 100, 101], cfg={"window_size": 4})
    last = out[-1]
    assert last["data_completeness"] == 100.0, last["data_completeness"]
    assert last["current_depth"] >= 99.0, last["current_depth"]
    print(f"OK — data_completeness=100 وcurrent_depth={last['current_depth']} قريب منها (استقرار)")


async def test_depth_equals_completeness_before_window_full():
    # §١١ — قبل امتلاء النافذة لا سلوك جديد: current_depth يبقى = data_completeness
    # كما كان قبل هذا التعديل (لا بيانات كافية بعد للحكم على الاستقرار).
    print("\n--- test_depth_equals_completeness_before_window_full ---")
    _atom, _bus, out = await _run([10, 90], cfg={"window_size": 4})
    last = out[-1]
    assert last["current_depth"] == last["data_completeness"], (
        last["current_depth"], last["data_completeness"])
    print(f"OK — نافذة غير ممتلئة ⇒ current_depth=data_completeness={last['current_depth']}")


async def main():
    tests = [test_mean_computed, test_rolling_window,
             test_contract_shape_complete, test_health_states,
             test_depth_diverges_from_completeness_when_unstable,
             test_depth_matches_completeness_when_stable,
             test_depth_equals_completeness_before_window_full]
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
