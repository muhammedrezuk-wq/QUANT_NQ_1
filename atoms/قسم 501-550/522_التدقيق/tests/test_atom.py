import asyncio
import json
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom522", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom522"] = _mod
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
        return AtomContext(atom_id=522, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _tmp_path():
    d = tempfile.mkdtemp(prefix="audit522_")
    return os.path.join(d, "audit.jsonl")


async def _new(config=None):
    cfg = {"log_path": _tmp_path(), "max_memory": 100}
    if config:
        cfg.update(config)
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg))
    await atom.start()
    return atom, bus, cfg["log_path"]


def _trail(bus):
    return [p for n, p in bus.published if n == EVENT_OUT][-1]


def _read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as h:
        return [json.loads(x) for x in h if x.strip()]


async def test_records_order():
    print("\n--- test_records_order ---")
    atom, bus, path = await _new()
    await atom._on_decision({"account_id": "A1", "symbol": "XAUUSD", "side": "BUY",
                             "volume": 0.02, "origin": "perpetual"})
    t = _trail(bus)
    assert t["total"] == 1 and t["recent"][-1]["kind"] == "order"
    assert t["recent"][-1]["symbol"] == "XAUUSD"
    lines = _read_lines(path)
    assert len(lines) == 1 and lines[0]["kind"] == "order"
    print("OK — أمر دخول → سطر order بالذاكرة + القرص")


async def test_records_manage_and_extract():
    print("\n--- test_records_manage_and_extract ---")
    atom, bus, path = await _new()
    await atom._on_manage({"action": "MODIFY_SL", "ticket": 5, "symbol": "XAUUSD", "stop_loss": 4300.0})
    await atom._on_extract({"account_id": "A1", "symbol": "XAUUSD", "amount": 25.0, "milestone": 50.0})
    kinds = [e["kind"] for e in _read_lines(path)]
    assert kinds == ["manage", "extract"]
    print("OK — تعديل + تخريج مسجَّلان")


async def test_state_change_only_changed():
    print("\n--- test_state_change_only_changed ---")
    atom, bus, path = await _new()
    await atom._on_portfolio({"portfolios": [
        {"account_id": "A1", "symbol": "XAUUSD", "state": "WARNING", "changed": True},
        {"account_id": "A1", "symbol": "USTEC", "state": "NORMAL", "changed": False}]})
    lines = _read_lines(path)
    assert len(lines) == 1 and lines[0]["symbol"] == "XAUUSD" and lines[0]["kind"] == "state_change"
    print("OK — الحالات المتغيّرة فقط تُسجَّل")


async def test_memory_bounded():
    print("\n--- test_memory_bounded ---")
    atom, bus, path = await _new({"max_memory": 3})
    for i in range(5):
        await atom._on_manage({"action": "MODIFY_SL", "ticket": i, "symbol": "XAUUSD"})
    t = _trail(bus)
    assert len(t["recent"]) == 3 and t["total"] == 5
    assert len(_read_lines(path)) == 5, "القرص يحتفظ بالكلّ (append-only)"
    print("OK — الذاكرة محصورة (3) · القرص كامل (5)")


async def test_ts_from_event():
    print("\n--- test_ts_from_event ---")
    atom, bus, path = await _new()
    await atom._on_trade({"symbol": "XAUUSD", "event_type": "CLOSE", "profit": 12.5, "timestamp": 1700.0})
    e = _read_lines(path)[0]
    assert e["ts"] == 1700.0 and e["kind"] == "trade_event" and e["profit"] == 12.5
    print("OK — وقت الحدث يُؤخذ من الحدث (بلا ساعة)")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"log_path": _tmp_path(), "max_memory": 10}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→HEALTHY")


async def main():
    tests = [test_records_order, test_records_manage_and_extract, test_state_change_only_changed,
             test_memory_bounded, test_ts_from_event, test_health]
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
