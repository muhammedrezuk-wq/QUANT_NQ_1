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
    "_atom571", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom571"] = _mod
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
        return AtomContext(atom_id=571, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _new(config=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(config if config is not None else {}))
    await atom.start()
    return atom, bus


async def _stop(atom, symbol, p_stop, computable=True, account="A1", source="525"):
    # حكم المالك ٢٠٢٦-٠٨-١٣ (البند ٢): `risk.hard_stop.price` له ناشران —
    # 525 صاحب الحدث، و512 ينشره أيضًا. آخر-من-كتب-يفوز كان يجعل القيمة
    # تتعلّق بترتيب الوصول، فصار 571 يسمّي مصدره صراحةً.
    await atom._on_hard_stop({"source": source,
                              "stops": [{"account_id": account, "symbol": symbol,
                                         "p_stop": p_stop, "computable": computable,
                                         "direction": "LONG"}]})


async def _portfolio(atom, symbol, state="NORMAL", intent="NONE", v_net=0.0, account="A1", halted=False):
    await atom._on_portfolio({"portfolios": [{"account_id": account, "symbol": symbol,
                                              "state": state, "protection_intent": intent,
                                              "v_net": v_net}], "halted": halted})


def _plan(bus, symbol):
    payload = [p for n, p in bus.published if n == EVENT_OUT][-1]
    return [x for x in payload["plans"] if x["symbol"] == symbol][0]


async def test_maintain_stop():
    print("\n--- test_maintain_stop ---")
    atom, bus = await _new()
    await _stop(atom, "XAUUSD", 50.0)
    await _portfolio(atom, "XAUUSD", state="NORMAL", intent="NONE", v_net=1.0)
    p = _plan(bus, "XAUUSD")
    assert p["primary_action"] == "MAINTAIN_STOP" and p["stop_action"] == "MAINTAIN"
    assert p["stop_price"] == 50.0
    print("OK — NORMAL + P_stop محسوب → MAINTAIN_STOP @50")


async def test_hard_stop_source_is_deterministic():
    """نسخة 512 من نفس الحدث تُتجاهَل، فالقيمة لا تتعلّق بترتيب الوصول."""
    print("\n--- test_hard_stop_source_is_deterministic ---")
    atom, bus = await _new()
    await _stop(atom, "XAUUSD", 50.0, source="525")
    await _stop(atom, "XAUUSD", 999.0, source="512")   # الدخيل
    await _portfolio(atom, "XAUUSD", state="NORMAL", intent="NONE", v_net=1.0)
    p = _plan(bus, "XAUUSD")
    assert p["stop_price"] == 50.0, "512 ما يجوز يطمس 525"
    assert atom._foreign_source == 1
    health = await atom.health_check()
    assert health.details["hard_stop_source"] == "525"
    print("OK — 525 وحده الحَكَم · تجاهَل 512 مرّة واحدة")


async def test_hedge_long():
    print("\n--- test_hedge_long ---")
    atom, bus = await _new()
    await _stop(atom, "USTEC", 30.0)
    await _portfolio(atom, "USTEC", state="WARNING", intent="REQUEST_HEDGE", v_net=0.7)
    p = _plan(bus, "USTEC")
    assert p["primary_action"] == "HEDGE" and p["hedge_volume"] == 0.7 and p["hedge_side"] == "SELL"
    assert p["stop_action"] == "REMOVE_AFTER_HEDGE", "الستوب يُشال بعد التحوّط (ترتيب P0-7)"
    print("OK — REQUEST_HEDGE · V_net+0.7 → HEDGE 0.7 SELL · شيل الستوب بعد التحوّط")


async def test_hedge_short():
    print("\n--- test_hedge_short ---")
    atom, bus = await _new()
    await _portfolio(atom, "XAUUSD", state="WARNING", intent="REQUEST_HEDGE", v_net=-0.5)
    p = _plan(bus, "XAUUSD")
    assert p["hedge_volume"] == 0.5 and p["hedge_side"] == "BUY"
    print("OK — V_net−0.5 → تحوّط 0.5 BUY (معاكس)")


async def test_freeze():
    print("\n--- test_freeze ---")
    atom, bus = await _new()
    await _stop(atom, "XAUUSD", 50.0)
    await _portfolio(atom, "XAUUSD", state="FROZEN", intent="FREEZE", v_net=1.0)
    p = _plan(bus, "XAUUSD")
    assert p["primary_action"] == "FREEZE" and p["protection"] == "FREEZE"
    print("OK — FROZEN → FREEZE (فوق كلّ شي)")


async def test_hold_system_dead():
    print("\n--- test_hold_system_dead ---")
    atom, bus = await _new()
    await _portfolio(atom, "XAUUSD", state="WARNING", intent="HOLD", v_net=1.0)
    p = _plan(bus, "XAUUSD")
    assert p["primary_action"] == "HOLD"
    print("OK — نظام ميت → HOLD (ستوب البروكر يمسك)")


async def test_monitor():
    print("\n--- test_monitor ---")
    atom, bus = await _new()
    await _portfolio(atom, "XAUUSD", state="NORMAL", intent="NONE", v_net=1.0)
    p = _plan(bus, "XAUUSD")
    assert p["primary_action"] == "MONITOR" and p["stop_action"] == "NONE"
    print("OK — NORMAL بلا P_stop → MONITOR")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await _portfolio(atom, "XAUUSD")
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def main():
    tests = [test_maintain_stop, test_hedge_long, test_hedge_short, test_freeze,
             test_hold_system_dead, test_monitor, test_health]
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
