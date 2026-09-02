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
    "_atom551", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom551"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom
EVENT_OUT = _mod.EVENT_OUT
EVENT_SKIPPED = _mod.EVENT_SKIPPED

CFG = {"reward_risk": 2.0, "magic": 20260801}


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
        return AtomContext(atom_id=551, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


def _size(symbol="NQ100", price=100.0, buy_lot=0.1, buy_stop=99.0,
          sell_lot=0.1, sell_stop=101.0):
    return {"account_id": "ACC", "broker": "BR", "symbol": symbol, "metadata": {"price": price, "buy_lot": buy_lot,
            "buy_stop": buy_stop, "sell_lot": sell_lot, "sell_stop": sell_stop}}


def _val(side, approved=True, symbol="NQ100", request_id="R1"):
    return {"request_id": request_id, "account_id": "ACC", "broker": "BR", "symbol": symbol, "side": side,
            "approved": approved, "reason": "", "kill_switch_state": False}


async def _new(cfg=None):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(cfg or dict(CFG)))
    await atom.start()
    return atom, bus


def _outs(bus):
    return [p for n, p in bus.published if n == EVENT_OUT]


def _skips(bus):
    return [p for n, p in bus.published if n == EVENT_SKIPPED]


async def test_build_buy():
    print("\n--- test_build_buy ---")
    atom, bus = await _new()
    await atom._on_account({"account_id": "ACC", "broker": "BR"})
    await atom._on_size(_size())
    await atom._on_validated(_val("BUY"))
    o = _outs(bus)[-1]
    assert o["side"] == "BUY" and o["volume"] == 0.1
    assert o["stop_loss"] == 99.0
    assert o["take_profit"] == 102.0, o["take_profit"]  # 100 + 2*(100-99)
    assert o["account_id"] == "ACC"
    print("OK — BUY: lot=0.1 stop=99 target=102")


async def test_build_sell():
    print("\n--- test_build_sell ---")
    atom, bus = await _new()
    await atom._on_size(_size())
    await atom._on_validated(_val("SELL"))
    o = _outs(bus)[-1]
    assert o["side"] == "SELL" and o["stop_loss"] == 101.0
    assert o["take_profit"] == 98.0, o["take_profit"]  # 100 - 2*(101-100)
    print("OK — SELL: stop=101 target=98")


async def test_skip_not_approved():
    print("\n--- test_skip_not_approved ---")
    atom, bus = await _new()
    await atom._on_size(_size())
    await atom._on_validated(_val("BUY", approved=False))
    assert len(_outs(bus)) == 0, "غير معتمَد لا يُبنى"
    # قبل الإصلاح: تجاهل صامت تمامًا، ولا حدث ولا سبب. الآن لازم ينشر سببه
    # المحدَّد — لا يُخلط بأي سبب تجاهل آخر بالذرّة.
    s = _skips(bus)[-1]
    assert s["reason"] == "UPSTREAM_REJECTED", s["reason"]
    print("OK — غير معتمَد → لا أمر، وسبب UPSTREAM_REJECTED منشور")


async def test_skip_no_size():
    print("\n--- test_skip_no_size ---")
    atom, bus = await _new()
    await atom._on_validated(_val("BUY"))
    assert len(_outs(bus)) == 0, "بلا تحجيم لا يُبنى"
    s = _skips(bus)[-1]
    assert s["reason"] == "NO_SIZE_YET", s["reason"]
    print("OK — بلا تحجيم → لا أمر، وسبب NO_SIZE_YET منشور")


async def test_skip_bad_stop_side():
    print("\n--- test_skip_bad_stop_side ---")
    atom, bus = await _new()
    # buy_stop above price -> invalid buy stop -> None from 513 normally; simulate missing
    await atom._on_size(_size(buy_lot=None, buy_stop=None))
    await atom._on_validated(_val("BUY"))
    assert len(_outs(bus)) == 0, "ستوب شراء غائب لا يُبنى"
    s = _skips(bus)[-1]
    assert s["reason"] == "INCOMPLETE_SIZE_DATA", s["reason"]
    print("OK — ستوب غائب → لا أمر، وسبب INCOMPLETE_SIZE_DATA منشور — مختلف عن NO_SIZE_YET")


async def test_skip_bad_symbol_or_side():
    print("\n--- test_skip_bad_symbol_or_side ---")
    atom, bus = await _new()
    await atom._on_size(_size())
    bad = _val("HOLD")  # اتجاه مو شراء ولا بيع، وما بيدخل المسار المباشر أصلًا
    await atom._on_validated(bad)
    assert len(_outs(bus)) == 0
    s = _skips(bus)[-1]
    assert s["reason"] == "BAD_SYMBOL_OR_SIDE", s["reason"]
    print("OK — جهة غير صالحة → سبب BAD_SYMBOL_OR_SIDE منشور")


async def test_skip_invalid_risk_distance():
    print("\n--- test_skip_invalid_risk_distance ---")
    atom, bus = await _new()
    # ستوب الشراء فوق السعر (بالمقلوب) → مسافة مخاطرة سالبة، سبب فيزيائي
    # مختلف عن كل الأسباب التانية — لازم يميّز لحاله.
    await atom._on_size(_size(buy_stop=101.0))
    await atom._on_validated(_val("BUY"))
    assert len(_outs(bus)) == 0
    s = _skips(bus)[-1]
    assert s["reason"] == "INVALID_RISK_DISTANCE", s["reason"]
    print("OK — مسافة مخاطرة غير صالحة → سبب INVALID_RISK_DISTANCE منشور")


async def test_skip_reasons_breakdown_in_health():
    print("\n--- test_skip_reasons_breakdown_in_health ---")
    atom, bus = await _new()
    await atom._on_validated(_val("BUY"))               # NO_SIZE_YET
    await atom._on_validated(_val("BUY", approved=False))  # UPSTREAM_REJECTED
    h = await atom.health_check()
    breakdown = h.details["skip_reasons"]
    assert breakdown.get("NO_SIZE_YET") == 1, breakdown
    assert breakdown.get("UPSTREAM_REJECTED") == 1, breakdown
    assert h.details["skipped"] == 2
    print("OK — health_check يفصّل الأسباب لا رقمًا واحدًا أعمى:", breakdown)


async def test_skip_no_size_carries_513_reason():
    print("\n--- test_skip_no_size_carries_513_reason ---")
    atom, bus = await _new()
    await atom._on_account({"account_id": "ACC", "broker": "BR"})
    # T2: 513 rejects sizing for this scope with a real reason it actually
    # emits (atoms/513: STALE_ACCOUNT_SYMBOL_SPECS) -- never invented here.
    await atom._on_size_rejected({"account_id": "ACC", "broker": "BR",
                                   "symbol": "NQ100", "reason": "STALE_ACCOUNT_SYMBOL_SPECS",
                                   "status": "REJECTED", "usable_for_order": False})
    await atom._on_validated(_val("BUY"))
    assert len(_outs(bus)) == 0
    s = _skips(bus)[-1]
    assert s["reason"] == "NO_SIZE_YET", s["reason"]
    assert s["sizing_reason"] == "STALE_ACCOUNT_SYMBOL_SPECS", s
    print("OK — رفض 513 (STALE_ACCOUNT_SYMBOL_SPECS) لا يضيع: يمرّ كـsizing_reason فوق NO_SIZE_YET")


async def test_skip_no_size_without_upstream_reason_stays_honest():
    print("\n--- test_skip_no_size_without_upstream_reason_stays_honest ---")
    atom, bus = await _new()
    await atom._on_validated(_val("BUY"))
    s = _skips(bus)[-1]
    assert s["reason"] == "NO_SIZE_YET", s["reason"]
    assert "sizing_reason" not in s, s
    print("OK — بلا رفض 513 مسجَّل: لا sizing_reason مخترع")


async def test_size_rejection_cleared_by_fresh_size():
    print("\n--- test_size_rejection_cleared_by_fresh_size ---")
    atom, bus = await _new()
    await atom._on_account({"account_id": "ACC", "broker": "BR"})
    await atom._on_size_rejected({"account_id": "ACC", "broker": "BR",
                                   "symbol": "NQ100", "reason": "NO_ACCOUNT_EQUITY",
                                   "status": "REJECTED"})
    # تحجيم صالح يصل لاحقًا لنفس النطاق -- الرفض القديم لازم ينمحي
    await atom._on_size(_size())
    await atom._on_validated(_val("BUY"))
    o = _outs(bus)[-1]
    assert o["side"] == "BUY" and o["volume"] == 0.1, o
    assert len(_skips(bus)) == 0, "تحجيم صالح وصل -- ما في تخطّي ولا رفض قديم عالق"
    print("OK — تحجيم صالح لاحق يمسح رفض 513 القديم لنفس النطاق")


async def test_skip_carries_decision_identity():
    print("\n--- test_skip_carries_decision_identity ---")
    atom, bus = await _new()
    v = _val("BUY", approved=False)
    v["decision_id"] = "dec:1"
    v["gate_request_id"] = "gate:1"
    await atom._on_validated(v)
    s = _skips(bus)[-1]
    assert s["decision_id"] == "dec:1" and s["gate_request_id"] == "gate:1", s
    print("OK — التخطّي يحمل هوية القرار كما وصلت (decision_id/gate_request_id)")


async def test_skip_identity_absent_stays_none():
    print("\n--- test_skip_identity_absent_stays_none ---")
    atom, bus = await _new()
    await atom._on_validated(_val("BUY", approved=False))
    s = _skips(bus)[-1]
    assert s["decision_id"] is None and s["gate_request_id"] is None, s
    print("OK — الهوية الغائبة تمرّ None صريحة -- لا اختراع")


async def test_health():
    print("\n--- test_health ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(dict(CFG)))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    ready = await atom.health_check()
    assert ready.state == HealthState.HEALTHY and ready.message.startswith("READY")
    await atom._on_size(_size())
    await atom._on_validated(_val("BUY"))
    assert (await atom.health_check()).state == HealthState.HEALTHY
    print("OK — الصحة تتدرّج UNHEALTHY→HEALTHY(جاهز، صفر مدخل)→HEALTHY(يعمل)")


async def test_direct_path_unguarded_stop_is_caught_by_584_downstream():
    """Item 22/27 of the 27-atom review ("2 payload shapes + an unguarded
    stop on one path -- adjusted later by 584"): _direct_order() builds an
    order from an already-priced/sized validated payload with ZERO
    validation on stop_loss -- payload.get("stop_loss") passes straight
    through, even as None or on the wrong side of reference_price (the
    sized path DOES validate this via risk_dist <= 0.0; the direct path
    never does). Traced the real pipeline wiring in the manifests, not
    assumed: 552 and 601 (the atom that actually writes to the broker
    bridge) subscribe only to 584's execution.order.legal /
    trading.final_decision -- never to this atom's raw
    execution.order.built. So 584 genuinely gates real execution; this
    end-to-end test proves it with the real 551+584 code, not a
    hand-simulated payload."""
    print("\n--- test_direct_path_unguarded_stop_is_caught_by_584_downstream ---")
    import importlib.util as ilu
    root584 = _Path(__file__).resolve().parents[2] / "584_شرعية_الستوب"
    spec584 = ilu.spec_from_file_location("_cross22_584", root584 / "atom.py")
    mod584 = ilu.module_from_spec(spec584)
    sys.modules["_cross22_584"] = mod584
    spec584.loader.exec_module(mod584)

    class _DispatchBus:
        def __init__(self):
            self.published = []
            self._handlers = {}

        def subscribe(self, name, handler):
            self._handlers.setdefault(name, []).append(handler)

        async def publish(self, name, payload):
            self.published.append((name, payload))
            for h in list(self._handlers.get(name, [])):
                await h(payload)

        def make_context(self, atom_id, config):
            return AtomContext(atom_id=atom_id, config=config, logger=_NullLogger(),
                               publish=self.publish, subscribe=self.subscribe)

    bus = _DispatchBus()
    atom551 = Atom()
    await atom551.initialize(bus.make_context(551, dict(CFG)))
    await atom551.start()

    atom584 = mod584.Atom()
    await atom584.initialize(bus.make_context(584, {"stop_buffer": 0, "reward_risk": 2.0}))
    await atom584.start()
    await atom584._on_specs({"symbols": [{"account_id": "ACC", "symbol": "NQ100",
                                          "point": 1.0, "stops_level": 2,
                                          "volume_min": 0.1, "volume_step": 0.1,
                                          "volume_max": 10}]})

    # مسار مباشر: الحمولة تحمل volume+reference_price جاهزَين، فـ_direct_order
    # تبني الأمر بلا أي نظرة على stop_loss إطلاقاً -- مُسقَط عمدًا هنا.
    direct_payload = {
        "request_id": "R-direct", "account_id": "ACC", "broker": "BR",
        "symbol": "NQ100", "side": "BUY", "action": "OPEN",
        "approved": True, "reason": "", "kill_switch_state": False,
        "volume": 1.0, "reference_price": 100.0,
    }
    await atom551._on_validated(direct_payload)

    built = [p for n, p in bus.published if n == EVENT_OUT]
    assert built and built[-1]["stop_loss"] is None, (
        "551 يجب أن يبني الأمر بلا أي تحقّق من الستوب على المسار المباشر: %r" % built)
    legal = [p for n, p in bus.published if n == mod584.EVENT_LEGAL]
    rejected = [p for n, p in bus.published if n == mod584.EVENT_REJECTED]
    assert not legal, "584 لا يجوز أن يُجيز أمراً بستوب غائب: %r" % legal
    assert rejected and rejected[-1]["reason"] == "INCOMPLETE_ORDER", (
        "584 يجب أن يرفض الأمر (ستوب غائب) قبل trading.final_decision: %r" % rejected)
    print("OK — ستوب المسار المباشر غير المحروس بـ٥٥١ يُرفَض فعليًّا بـ٥٨٤ قبل التنفيذ الحقيقي")


async def main():
    tests = [test_build_buy, test_build_sell, test_skip_not_approved,
             test_skip_no_size, test_skip_bad_stop_side, test_health,
             test_skip_bad_symbol_or_side, test_skip_invalid_risk_distance,
             test_skip_reasons_breakdown_in_health,
             test_skip_no_size_carries_513_reason,
             test_skip_no_size_without_upstream_reason_stays_honest,
             test_size_rejection_cleared_by_fresh_size,
             test_skip_carries_decision_identity,
             test_skip_identity_absent_stays_none,
             test_direct_path_unguarded_stop_is_caught_by_584_downstream]
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
