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
    "_atom513", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom513"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT

CFG = {"risk_per_trade_pct": 1.0, "default_stop_pct": 0.5, "min_lot": 0.01,
       "max_lot": 1.0, "lot_step": 0.01}


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
        return AtomContext(atom_id=513, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _account(equity):
    """ورقة ١٥ §٦ — حقوق الملكية من 654 لا من 619."""
    return {"account_id": "ACC", "broker": "MT5", "equity": equity}


def _specs(tick_value=1.0, tick_size=0.01, symbol="NQ100"):
    return {"account_id": "ACC", "broker": "MT5", "provider": "MT5", "symbols": [
        {"symbol": symbol, "contract_size": 1, "tick_value": tick_value,
         "tick_size": tick_size}]}


def _tick(price=100.0, symbol="NQ100"):
    return {"account_id": "ACC", "broker": "MT5", "symbol": symbol, "timeframe": "tick", "sequence": "0",
            "timestamp": 0.0, "price": price}


def _stop(buy_stop=None, sell_stop=None, symbol="NQ100"):
    return {"account_id": "ACC", "broker": "MT5", "symbol": symbol,
            "metadata": {"buy_stop": buy_stop, "sell_stop": sell_stop}}


async def _new(cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    return atom, bus


def _outs(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


async def test_computed_lot():
    print("\n--- test_computed_lot ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(1000.0))
    await atom._on_specs(_specs())
    await atom._on_tick(_tick(100.0))
    last = _outs(bus)[-1]
    # risk=1000*1%=10 ; stop=100*0.5%=0.5 ; value_per_unit=1/0.01=100 ; lot=10/(0.5*100)=0.2
    assert last["metadata"]["lot"] == 0.2, last["metadata"]["lot"]
    assert last["metadata"]["risk_amount"] == 10.0
    print(f"OK — لوت محسوب = {last['metadata']['lot']} (risk=10)")


async def test_clamp_to_max():
    print("\n--- test_clamp_to_max ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(10000000.0))  # huge equity -> lot capped at max
    await atom._on_specs(_specs())
    await atom._on_tick(_tick(100.0))
    assert _outs(bus)[-1]["metadata"]["lot"] == 1.0
    print("OK — قصّ عند max_lot=1.0")


async def test_no_equity_no_size():
    print("\n--- test_no_equity_no_size ---")
    atom, bus = await _new()
    await atom._on_specs(_specs())
    await atom._on_tick(_tick(100.0))
    assert len(_outs(bus)) == 0, "بلا Equity لا يحجّم"
    h = await atom.health_check()
    assert h.state == HealthState.DEGRADED and h.message == "NO_EQUITY_YET"
    print("OK — بلا Equity → لا تحجيم + DEGRADED")


async def test_no_specs_no_size():
    print("\n--- test_no_specs_no_size ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(1000.0))
    await atom._on_tick(_tick(100.0))
    assert len(_outs(bus)) == 0, "بلا مواصفات لا يحجّم"
    print("OK — بلا مواصفات → لا تحجيم")


async def test_health_healthy():
    print("\n--- test_health_healthy ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    await atom._on_truth_equity(_account(1000.0))
    await atom._on_specs(_specs())
    await atom._on_tick(_tick(100.0))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة HEALTHY عند اكتمال المدخلات")


async def test_structure_lots():
    print("\n--- test_structure_lots ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(1000.0))
    await atom._on_specs(_specs())
    await atom._on_stop(_stop(buy_stop=99.0, sell_stop=101.0))
    await atom._on_tick(_tick(100.0))
    m = _outs(bus)[-1]["metadata"]
    # buy: dist=100-99=1 ; value_per_unit=1/0.01=100 ; lot=10/(1*100)=0.1
    assert m["buy_lot"] == 0.1, m["buy_lot"]
    assert m["buy_stop"] == 99.0
    # sell: dist=101-100=1 ; lot=0.1
    assert m["sell_lot"] == 0.1, m["sell_lot"]
    assert m["sell_stop"] == 101.0
    # default lot still present (back-compat)
    assert m["lot"] == 0.2, m["lot"]
    print("OK — لوت هيكليّ buy/sell=0.1 + الافتراضيّ 0.2 محفوظ")


async def test_symbol_from_other_broker_is_rejected():
    """4.3.0: a unique contract under another broker is still unsafe."""
    print("\n--- test_symbol_from_other_broker_is_rejected ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(1000.0))
    await atom._on_account({"account_id": "ACC", "broker": "ServerA"})
    bridge_specs = {"provider": "MT5", "symbols": [
        {"account_id": "ACC", "symbol": "NQ100", "contract_size": 1,
         "tick_value": 1.0, "tick_size": 0.01}]}
    await atom._on_specs(bridge_specs)
    await atom._on_tick({**_tick(100.0), "broker": "CompanyB"})
    assert not _outs(bus), "cross-broker contract must never size"
    rejected = [p for n, p in bus.published if n == _mod.EVENT_REJECTED]
    assert rejected[-1]["reason"] == "SIZING_UNAVAILABLE_FOR_SYMBOL"
    print("OK — مواصفة وسيط آخر تُرفض بلا تخمين")


async def test_symbol_fallback_ambiguous_stays_strict():
    """الوحدة ٢ (4.2.0): وسيطان مختلفان لنفس (الحساب·الرمز) = غموض — يبقى الرفض الصارم."""
    print("\n--- test_symbol_fallback_ambiguous_stays_strict ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(1000.0))
    await atom._on_specs({"account_id": "ACC", "broker": "B1", "symbols": [
        {"symbol": "NQ100", "contract_size": 1, "tick_value": 1.0, "tick_size": 0.01}]})
    await atom._on_specs({"account_id": "ACC", "broker": "B2", "symbols": [
        {"symbol": "NQ100", "contract_size": 1, "tick_value": 1.0, "tick_size": 0.01}]})
    await atom._on_tick({**_tick(100.0), "broker": "B3"})
    assert not _outs(bus), "ambiguous broker fallback must not size"
    h = await atom.health_check()
    assert h.message == "SIZING_UNAVAILABLE_FOR_SYMBOL", h.message
    assert h.details["symbol_fallback_sized"] == 0, h.details
    print("OK — وسيطان لنفس الرمز = رفض صارم بلا تخمين")


async def test_tick_teaches_broker_unsticks_pending():
    """الوحدة ٢ (4.2.0): مواصفات تصل قبل أي حالة حساب → تنتظر؛ أول تِكّة
    بهويّة كاملة تعلّم الوسيط وتُخزَّن المواصفة المنتظرة في نفس التِكّة."""
    print("\n--- test_tick_teaches_broker_unsticks_pending ---")
    atom, bus = await _new()
    await atom._on_truth_equity(_account(1000.0))
    bridge_specs = {"provider": "MT5", "symbols": [
        {"account_id": "ACC", "symbol": "NQ100", "contract_size": 1,
         "tick_value": 1.0, "tick_size": 0.01}]}
    await atom._on_specs(bridge_specs)              # no broker known yet -> pending
    h0 = await atom.health_check()
    assert h0.details["pending_specs"] == 1, h0.details
    assert not _outs(bus)
    await atom._on_tick(_tick(100.0))               # broker MT5 learned here
    outs = _outs(bus)
    assert outs and outs[-1]["status"] == "OK", "pending spec never unstuck"
    h1 = await atom.health_check()
    assert h1.details["pending_specs"] == 0, h1.details
    print("OK — التِكّة تُعلّم الوسيط وتُحرّر المواصفة المعلّقة في اللحظة نفسها")


async def main():
    tests = [test_computed_lot, test_clamp_to_max, test_no_equity_no_size,
             test_no_specs_no_size, test_health_healthy, test_structure_lots,
             test_symbol_from_other_broker_is_rejected,
             test_symbol_fallback_ambiguous_stays_strict,
             test_tick_teaches_broker_unsticks_pending]
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
