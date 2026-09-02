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
    "_atom525", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom525"] = _mod
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
        return AtomContext(atom_id=525, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _new():
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"min_abs_v_net": 1e-9}))
    await atom.start()
    return atom, bus


async def _specs(atom, symbol="XAUUSD", tick_value=1.0, tick_size=1.0):
    await atom._on_account({"account_id": "A1", "broker": "BR"})
    await atom._on_specs({"account_id": "A1", "broker": "BR", "provider": "MT5", "symbols": [
        {"symbol": symbol, "contract_size": 100.0, "tick_value": tick_value, "tick_size": tick_size}]})


def _led(symbol, v_net, w, budget=50.0, buffer_k=0.0, commission_est=0.0, budgeted=True, account="A1"):
    return {"account_id": account, "broker": "BR", "symbol": symbol, "v_net": v_net, "w": w,
            "budget": budget, "buffer_k": buffer_k, "commission_est": commission_est,
            "budgeted": budgeted}


async def _feed(atom, *leds):
    await atom._on_ledger({"ledgers": list(leds)})


def _stop(bus, symbol):
    payload = [p for n, p in bus.published if n == EVENT_OUT][-1]
    return [s for s in payload["stops"] if s["symbol"] == symbol][0]


def _net_at(price, s, budget, buffer_k=0.0, commission_est=0.0):
    return buffer_k + s["vpu"] * (price * s["v_net"] - _W[s["symbol"]]) - commission_est


_W = {}


async def test_long_stop_basic():
    print("\n--- test_long_stop_basic ---")
    atom, bus = await _new()
    await _specs(atom)
    _W["XAUUSD"] = 100.0
    await _feed(atom, _led("XAUUSD", 1.0, 100.0, budget=50.0))
    s = _stop(bus, "XAUUSD")
    assert s["computable"] and s["direction"] == "LONG"
    assert s["p_stop"] == 50.0 and s["delta_p"] == 50.0 and s["avg_entry"] == 100.0
    assert abs(_net_at(s["p_stop"], s, 50.0) + 50.0) < 1e-6, "الصافي عند P_stop لازم = −R"
    print("OK — P_stop=50 · ΔP=50 · عند P_stop الصافي=−R بالضبط")


async def test_counter_profit_widens_stop():
    print("\n--- test_counter_profit_widens_stop ---")
    atom, bus = await _new()
    await _specs(atom, symbol="USTEC")
    _W["USTEC"] = 100.0
    await _feed(atom, _led("USTEC", 1.0, 100.0, budget=50.0, buffer_k=20.0))
    s = _stop(bus, "USTEC")
    assert s["p_stop"] == 30.0 and s["delta_p"] == 70.0
    assert abs(_net_at(s["p_stop"], s, 50.0, buffer_k=20.0) + 50.0) < 1e-6
    print("OK — K=20 → P_stop=30 · ΔP=70 (توسّع الستوب من الربح العكسيّ · «$50→$70»)")


async def test_short_stop():
    print("\n--- test_short_stop ---")
    atom, bus = await _new()
    await _specs(atom)
    _W["XAUUSD"] = -100.0
    await _feed(atom, _led("XAUUSD", -1.0, -100.0, budget=50.0))
    s = _stop(bus, "XAUUSD")
    assert s["direction"] == "SHORT" and s["p_stop"] == 150.0 and s["delta_p"] == 50.0
    assert abs(_net_at(s["p_stop"], s, 50.0) + 50.0) < 1e-6
    print("OK — صافي بيع → الستوب فوق الدخول (150) · الصافي=−R")


async def test_vpu_scaling():
    print("\n--- test_vpu_scaling ---")
    atom, bus = await _new()
    await _specs(atom, tick_value=10.0, tick_size=1.0)
    _W["XAUUSD"] = 100.0
    await _feed(atom, _led("XAUUSD", 1.0, 100.0, budget=50.0))
    s = _stop(bus, "XAUUSD")
    assert s["vpu"] == 10.0 and s["delta_p"] == 5.0 and s["p_stop"] == 95.0
    print("OK — vpu=10 → ΔP=5 · P_stop=95 (قيمة النقطة تدخل الحساب)")


async def test_commission_tightens_stop():
    print("\n--- test_commission_tightens_stop ---")
    atom, bus = await _new()
    await _specs(atom)
    _W["XAUUSD"] = 100.0
    await _feed(atom, _led("XAUUSD", 1.0, 100.0, budget=50.0, commission_est=10.0))
    s = _stop(bus, "XAUUSD")
    assert s["delta_p"] == 40.0 and s["p_stop"] == 60.0
    assert abs(_net_at(s["p_stop"], s, 50.0, commission_est=10.0) + 50.0) < 1e-6
    print("OK — عمولة $10 تقلّص المجال → ΔP=40 (محافِظ)")


async def test_flat_no_price_stop():
    print("\n--- test_flat_no_price_stop ---")
    atom, bus = await _new()
    await _specs(atom)
    await _feed(atom, _led("XAUUSD", 0.0, 0.0, budget=50.0))
    s = _stop(bus, "XAUUSD")
    assert s["computable"] is False and s["reason"] == "FLAT_NO_PRICE_STOP"
    print("OK — V_net=0 (متحوّط) → لا ستوب سعريّ")


async def test_no_specs():
    print("\n--- test_no_specs ---")
    atom, bus = await _new()
    await _feed(atom, _led("BTCUSD", 1.0, 100.0, budget=50.0))
    s = _stop(bus, "BTCUSD")
    assert s["computable"] is False and s["reason"] == "NO_ACCOUNT_SYMBOL_SPECS"
    print("OK — بلا مواصفات → NO_SPECS (لا نخمّن vpu)")


async def test_no_budget():
    print("\n--- test_no_budget ---")
    atom, bus = await _new()
    await _specs(atom)
    await _feed(atom, _led("XAUUSD", 1.0, 100.0, budget=None, budgeted=False))
    s = _stop(bus, "XAUUSD")
    assert s["computable"] is False and s["reason"] == "NO_BUDGET"
    print("OK — بلا ميزانيّة → NO_BUDGET")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    assert (await atom.health_check()).state == HealthState.DEGRADED
    await _specs(atom)
    _W["XAUUSD"] = 100.0
    await _feed(atom, _led("XAUUSD", 1.0, 100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة: UNHEALTHY→DEGRADED→HEALTHY")


async def test_zero_min_abs_v_net_config_does_not_crash_on_flat_position():
    """v2.1.0: min_abs_v_net=0 مسموح بالمخطّط القديم (minimum: 0) — قراءة
    طبيعية لإعداد يظنّه صاحبه «بلا تصفية». يحوّل حارس FLAT_NO_PRICE_STOP
    إلى abs(v_net) < 0 (لا يصحّ أبداً)، فيصل v_net=0.0 الحقيقي للقسمة
    مباشرة. أرضية صلبة غير قابلة للإعداد يجب أن تحمي بصرف النظر."""
    print("\n--- test_zero_min_abs_v_net_config_does_not_crash_on_flat_position ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context({"min_abs_v_net": 0}))
    await atom.start()
    await _specs(atom)
    await _feed(atom, _led("XAUUSD", 0.0, 0.0, budget=50.0))
    s = _stop(bus, "XAUUSD")
    assert s["computable"] is False and s["reason"] == "FLAT_NO_PRICE_STOP", s
    print("OK — v_net=0.0 مع min_abs_v_net=0 المُعَدّ: لا انهيار، FLAT_NO_PRICE_STOP صريح")


async def main():
    tests = [test_long_stop_basic, test_counter_profit_widens_stop, test_short_stop,
             test_vpu_scaling, test_commission_tightens_stop, test_flat_no_price_stop,
             test_no_specs, test_no_budget, test_health,
             test_zero_min_abs_v_net_config_does_not_crash_on_flat_position]
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
