# -*- coding: utf-8 -*-
"""عيار RISK_DIAL — حالات القبول المختومة (عقد المحورين v1.1 §3-ج، نصّ المالك).

D = 0   → لا فتح/إضافة جديدة → ولا تصفية للمركز القائم بسبب الـDial
D = 25  → يسمح بـ25% من الزيادة الممكنة (في الدورة)
D = 50  → يسمح بـ50%
D = 100 → يسمح بكل الزيادة التي تسمح بها E(S) + R_B + gross_cap + بقية الحواجز
S ينخفض → base_target < current_gross → decrease > 0 → التخفيض مسموح حتى لو D = 0

العيار يُحقن ببديل `_risk_dial` — لا كتابة على سجل العيارات الحي
(درس 166 المقيس: فحص كتب اعتمادًا حقيقيًّا بالقاعدة الحية ونُظّف).
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(folder))
spec = importlib.util.spec_from_file_location("_atom581_dial", folder / "atom.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["_atom581_dial"] = mod
spec.loader.exec_module(mod)

BANDS = {"0.0": 0.0, "0.2": 0.1, "0.4": 0.25, "0.6": 0.5, "0.9": 1.0}
HEDGE = {"0.0": 1.0, "0.2": 0.7, "0.4": 0.4, "0.6": 0.2, "0.9": 0.0}


class L:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class B:
    def __init__(self):
        self.e = []
        self.subs = {}

    def subscribe(self, n, h): self.subs[n] = h

    async def publish(self, n, p): self.e.append((n, p))

    def c(self):
        return mod.AtomContext(
            581,
            {"bands": BANDS, "hedge_bands": HEDGE, "s_enter": 0.20,
             "s_exit": 0.15, "max_target_volume": 20, "max_step_volume": 1,
             "min_volume": 0.01},
            L(), self.publish, self.subscribe)


def gate(payload, cycle):
    return dict(payload, cycle_id=cycle, decision_side=payload.get("direction"),
                approved=True, decision_id="d-" + cycle,
                gate_request_id="d-" + cycle + ":req1")


async def decide(a, payload, cycle="GOLD|60s|1"):
    payload = dict(payload, cycle_id=cycle)
    await a._on_verdict({"symbol": payload["symbol"], "cycle_id": cycle,
                         "metadata": {"approved": True}})
    await a._on_gate_passed(gate(payload, cycle))


async def prime(dial=100.0, ledger_extra=None):
    b = B()
    a = mod.Atom()
    await a.initialize(b.c())
    await a.start()
    a._risk_dial = lambda: dial
    await a._on_portfolio({"account_id": "A", "symbol": "GOLD", "state": "ACTIVE",
                           "system_alive": True, "account_mode": "HEDGING"})
    await a._on_specs({"symbols": [{"account_id": "A", "symbol": "GOLD",
                                    "tick_value": 1, "tick_size": 1}]})
    await a._on_tick({"account_id": "A", "symbol": "GOLD", "price": 100,
                      "bid": 99.5, "ask": 100.5})
    await a._on_dial({"profiles": [{"account_id": "A", "symbol": "GOLD",
                                    "stop_distance_frac": 0.05}]})
    row = {"account_id": "A", "symbol": "GOLD", "risk_budget": 50, "v_net": 0}
    row.update(ledger_extra or {})
    await a._on_ledger({"ledgers": [row]})
    return a, b


async def legs(a, buy, sell, ledger_extra=None):
    rows = []
    if buy > 0:
        rows.append({"account_id": "A", "symbol": "GOLD", "side": "BUY",
                     "ticket": 1, "volume": buy, "entry_price": 100})
    if sell > 0:
        rows.append({"account_id": "A", "symbol": "GOLD", "side": "SELL",
                     "ticket": 2, "volume": sell, "entry_price": 100})
    await a._on_positions({"source": "t", "account_id": "A", "positions": rows})
    row = {"account_id": "A", "symbol": "GOLD", "risk_budget": 50,
           "v_net": buy - sell}
    row.update(ledger_extra or {})
    await a._on_ledger({"ledgers": [row]})


async def main():
    # capacity = 50/(100×0.05×1) = 10 · S=0.8 → E=0.5 → base = 5.

    # D=100: كامل الزيادة — سلوك اليوم بلا تغيير.
    a, b = await prime(dial=100.0)
    await decide(a, {"account_id": "A", "symbol": "GOLD", "direction": "buy",
                     "score": 80, "strength": 0.8})
    p = b.e[-1][1]
    assert p["target_gross"] == 5.0 and p["risk_dial"] == 100.0, (p["target_gross"], p["risk_dial"])
    assert p["base_target"] == 5.0 and p["allowed_increase"] == 5.0, (p["base_target"], p["allowed_increase"])
    assert p["remaining_RB"] == 50.0 and p["remaining_add_budget"] == 50.0, (p["remaining_RB"], p["remaining_add_budget"])

    # D=0: لا فتح جديد من الصفر.
    a, b = await prime(dial=0.0)
    await decide(a, {"account_id": "A", "symbol": "GOLD", "direction": "buy",
                     "score": 80, "strength": 0.8})
    p = b.e[-1][1]
    assert p["target_gross"] == 0.0 and p["allowed_increase"] == 0.0, (p["target_gross"], p["allowed_increase"])
    assert p["action"] == "HOLD", p["action"]

    # D=0 مع مركز قائم gross=4 وbase=5 ≥ الحالي: لا زيادة ولا تصفية — يبقى 4.
    a, b = await prime(dial=0.0)
    await legs(a, 2.0, 2.0)
    await decide(a, {"account_id": "A", "symbol": "GOLD", "direction": "buy",
                     "score": 80, "strength": 0.8})
    p = b.e[-1][1]
    assert p["target_gross"] == 4.0 and p["decrease"] == 0.0, (p["target_gross"], p["decrease"])

    # D=25 و D=50: نسبة الزيادة الممكنة في الدورة (من الصفر: capE×D).
    for dial, want in ((25.0, 1.25), (50.0, 2.5)):
        a, b = await prime(dial=dial)
        await decide(a, {"account_id": "A", "symbol": "GOLD", "direction": "buy",
                         "score": 80, "strength": 0.8})
        p = b.e[-1][1]
        assert abs(p["target_gross"] - want) < 1e-9, (dial, p["target_gross"], want)
        assert abs(p["allowed_increase"] - want) < 1e-9, (dial, p["allowed_increase"])

    # مثال المالك: التحليل يضعف والمركز 10 — الخفض يتبع التحليل وحده حتى مع D=0.
    # S=0.3 → E=0.1 → base=1 < current=10 → الهدف ينزل إلى 1 مهما كان D.
    for dial in (0.0, 50.0, 100.0):
        a, b = await prime(dial=dial)
        await legs(a, 5.0, 5.0)
        await decide(a, {"account_id": "A", "symbol": "GOLD", "direction": "buy",
                         "score": 30, "strength": 0.3})
        p = b.e[-1][1]
        assert p["target_gross"] == 1.0 and p["decrease"] == 9.0, (dial, p["target_gross"], p["decrease"])
        assert p["allowed_increase"] == 0.0, (dial, p["allowed_increase"])

    # ميزانية الإضافة بحكم الدايل: consumed=max(u_float,u_realized)×R_B.
    # u_float=0.6 → consumed=30 · D=50% → dial_add=25−30=−5 ≤ 0 ⇒ لا دلتا موجبة.
    a, b = await prime(dial=50.0, ledger_extra={"u_float": 0.6, "u_realized": 0.2})
    await decide(a, {"account_id": "A", "symbol": "GOLD", "direction": "buy",
                     "score": 80, "strength": 0.8})
    p = b.e[-1][1]
    assert p["consumed_budget"] == 30.0 and p["remaining_RB"] == 20.0, (p["consumed_budget"], p["remaining_RB"])
    assert p["dial_add_budget"] == -5.0 and p["remaining_add_budget"] == 0.0, (p["dial_add_budget"], p["remaining_add_budget"])
    assert p["allowed_increase"] == 0.0 and p["target_gross"] == 0.0, (p["allowed_increase"], p["target_gross"])

    # الحياد يبقى حيادًا: S تحت المدخل مع مركز 3+3 — الدايل لا يصفّي المحايد.
    a, b = await prime(dial=0.0)
    await legs(a, 3.0, 3.0)
    await decide(a, {"account_id": "A", "symbol": "GOLD", "direction": "buy",
                     "score": 0, "strength": 0.10})
    p = b.e[-1][1]
    assert p["target_gross"] == 6.0 and p["reason"] == mod.REASON_NEUTRAL_KEEP, (p["target_gross"], p["reason"])

    # معالج العيار: أمر لعيار 581 يُعتمد ويُنشر حاله ويعاد الحساب —
    # ببديل apply_command (لا كتابة على السجل الحي).
    a, b = await prime(dial=100.0)
    real_apply = mod.apply_command
    calls = {}
    def fake_apply(payload, atom_id=""):
        calls["atom_id"] = atom_id
        if str(payload.get("name")) != "RISK_DIAL":
            return None
        return {"name": "RISK_DIAL", "value": 40.0, "version": 1}
    mod.apply_command = fake_apply
    try:
        await a._on_setting({"name": "RISK_DIAL", "value": 40.0,
                             "command_id": "c1", "operator": "nq",
                             "approved_at": 1.0})
    finally:
        mod.apply_command = real_apply
    assert calls["atom_id"] == "581"
    names = [n for n, _ in b.e]
    assert mod.EVENT_SETTINGS_STATE in names, names
    assert a._settings_applied == 1

    print("test_risk_dial: OK")


if __name__ == "__main__":
    asyncio.run(main())


def test_risk_dial_contract():
    asyncio.run(main())
