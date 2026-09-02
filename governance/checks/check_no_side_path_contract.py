"""ح٢ — حارس «لا مسار جانبي» (حزمة ح، بند ٢٢ من ختم NQ).

يثبت من الكود الحيّ لثلاث ذرّات فعليّة (467 بوّابة الإرسال، 576 المحرك
الدائم، 581 محرك فرق المركز) أن لا طريق دخول يلتفّ على البوّابة 467:

  * 467: لا يمرّ إلى `decision.gate.passed`/`decision.dispatch.state` قرار
    غير معتمد، ولا قرار بلا جانب معروف (buy/sell) — `decision.wait.*`
    يُسجَّل على `decision.gate.recorded` فقط بلا تنفيذ، والتكرار بلا
    `redispatch_reason` يُبلَع صامتًا (لا إرسال مضاعف).
  * 576 (`perpetual.asset.activate`): يرفض بـ`NO_PARENT_AUTHORITY` أي
    تفعيل بلا `parent_decision_id` أو `owner_command_id`/`command_id` —
    ولا ينشر `execution.order.requested` بلا أحدهما. **ومنذ الوحدة ١
    (2026-08-23): الـ`parent_decision_id` المُصرَّح يجب أن يكون قرارًا نشرت له
    467 فعلًا `decision.gate.passed` — والمُلفَّق يُرفض بـ
    `DECISION_NOT_IN_GATE_WINDOW` (T11) ولا يُنشر به أمر واحد.**
  * 581: قرار خام غير معتمد (`decision.resolved.state`) لا يتحول أبدًا
    إلى جانب شراء/بيع بمخرج `perpetual.target.state` — يبقى `wait` حتى
    يصل حدث بوابة حقيقي (`decision.gate.passed`) من 467.

كل حالة هنا مبنيّة على القراءة الفعليّة لـ`atom.py` الثلاثة (لا افتراض ولا
وصف) — راجع `سياق\\ح١ عينات القبول` وبند ح٢ بورقة ٩٨ حزمة ج.
Exit 0 فقط إذا نجحت كل الحالات؛ أي حالة فاشلة => exit 1 مع بيان أيّها.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)
sys.path.insert(0, str(ROOT))

from core.contracts.atom import AtomContext  # noqa: E402


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self) -> None:
        self.log: list[tuple[str, dict]] = []
        self.handlers: dict[str, list] = {}

    def subscribe(self, name: str, handler) -> None:
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name: str, payload: dict) -> None:
        self.log.append((name, dict(payload)))
        for handler in list(self.handlers.get(name, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def events(self, name: str) -> list[dict]:
        return [p for n, p in self.log if n == name]

    def events_where(self, name: str, **match: Any) -> list[dict]:
        rows = self.events(name)
        return [r for r in rows if all(r.get(k) == v for k, v in match.items())]


def load_atom(folder: str):
    directory = ATOM_ROOT / folder
    spec = importlib.util.spec_from_file_location(
        "_sidepath_" + folder.split("_")[0], directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


CONFIGS = {
    "467_إرسال_القرار": {},
    "576_المحرك_الدائم": {"lot_step": 0.01, "min_lot": 0.01, "max_lot": 20.0,
                            "fallback_stop_distance_frac": 0.005},
    "581_محرك_فرق_المركز": {
        "bands": {"0.0": 0.0, "0.2": 0.1, "0.4": 0.25, "0.6": 0.5},
        "hedge_bands": {"0.0": 1.0, "0.2": 0.7, "0.4": 0.4, "0.6": 0.2},
        "s_enter": 0.20, "s_exit": 0.15, "max_target_volume": 20,
        "max_step_volume": 1, "min_volume": 0.01, "hedge_cost_per_volume": 0.0},
}

ACCOUNT = "52992818"
BROKER = "Raw Trading Ltd"


async def run() -> int:
    bus = Bus()
    atoms = {}
    for index, (folder, config) in enumerate(CONFIGS.items()):
        module = load_atom(folder)
        atom = module.Atom()
        context = AtomContext(atom_id=1000 + index, config=config, logger=_Logger(),
                              publish=bus.publish, subscribe=bus.subscribe)
        await atom.initialize(context)
        await atom.start()
        atoms[folder.split("_")[0]] = atom

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        mark = "OK  " if ok else "FAIL"
        print("  [%s] %s%s" % (mark, label, ("  -- " + detail) if detail and not ok else ""))
        if not ok:
            failures.append(label)

    # ------------------------------------------------------------------
    print("== 467: no side path around approval/side/identity ==")

    async def send_467(**kw):
        await bus.publish("decision.approved.state", kw)

    # T1 — not approved => blocked only, never passed/dispatch for this id.
    await send_467(account_id=ACCOUNT, broker=BROKER, symbol="SYM1", timeframe="5m",
                    cycle_id="cyc-1", decision_id="dec:1", decision_side="buy",
                    approved=False, reason="TEST_NOT_APPROVED")
    blocked1 = bus.events_where("decision.gate.blocked", decision_id="dec:1")
    check("T1 not-approved -> gate.blocked only",
          bool(blocked1) and blocked1[-1].get("reject_reason") == "TEST_NOT_APPROVED"
          and not bus.events_where("decision.gate.passed", decision_id="dec:1")
          and not bus.events_where("decision.dispatch.state", decision_id="dec:1"),
          "blocked=%s" % blocked1)

    # T2 — approved but no usable side => blocked SIDE_UNKNOWN, never passed.
    await send_467(account_id=ACCOUNT, broker=BROKER, symbol="SYM1", timeframe="5m",
                    cycle_id="cyc-2", decision_id="dec:2", approved=True)
    blocked2 = bus.events_where("decision.gate.blocked", decision_id="dec:2")
    check("T2 approved+unknown-side -> gate.blocked SIDE_UNKNOWN",
          bool(blocked2) and blocked2[-1].get("reject_reason") == "SIDE_UNKNOWN"
          and not bus.events_where("decision.gate.passed", decision_id="dec:2"),
          "blocked=%s" % blocked2)

    # T3 — wait recorded regardless of approved flag; never blocked/passed/dispatch.
    await send_467(account_id=ACCOUNT, broker=BROKER, symbol="SYM1", timeframe="5m",
                    cycle_id="cyc-3", decision_id="dec:3", decision_side="wait",
                    approved=False)
    recorded3 = bus.events_where("decision.gate.recorded", decision_id="dec:3")
    check("T3 wait -> gate.recorded only, no gate/dispatch",
          bool(recorded3)
          and not bus.events_where("decision.gate.blocked", decision_id="dec:3")
          and not bus.events_where("decision.gate.passed", decision_id="dec:3")
          and not bus.events_where("decision.dispatch.state", decision_id="dec:3"),
          "recorded=%s" % recorded3)

    # T4 — the only legitimate path in: approved + real side + full identity.
    await send_467(account_id=ACCOUNT, broker=BROKER, symbol="SYM1", timeframe="5m",
                    cycle_id="cyc-4", decision_id="dec:4", decision_side="buy",
                    approved=True)
    passed4 = bus.events_where("decision.gate.passed", decision_id="dec:4")
    dispatch4 = bus.events_where("decision.dispatch.state", decision_id="dec:4")
    gate_request_id = passed4[-1].get("gate_request_id") if passed4 else None
    check("T4 approved+buy+full-identity -> gate.passed + dispatch.state",
          bool(passed4) and bool(dispatch4) and gate_request_id
          and dispatch4[-1].get("gate_request_id") == gate_request_id
          and dispatch4[-1].get("side") == "BUY" and dispatch4[-1].get("executable") is True,
          "passed=%s dispatch=%s" % (passed4, dispatch4))

    # T5 — replay of the exact same decision (no redispatch_reason) must not
    # re-dispatch: the gate is a single door, not a re-openable one.
    before = len(bus.events_where("decision.gate.passed", decision_id="dec:4"))
    await send_467(account_id=ACCOUNT, broker=BROKER, symbol="SYM1", timeframe="5m",
                    cycle_id="cyc-4", decision_id="dec:4", decision_side="buy",
                    approved=True)
    after = len(bus.events_where("decision.gate.passed", decision_id="dec:4"))
    check("T5 duplicate resend without redispatch_reason -> no re-dispatch",
          before == after == 1, "before=%d after=%d" % (before, after))

    # ------------------------------------------------------------------
    print("== 576: no side path around parent/owner authority ==")

    # Feed enough live-shaped market state so an authorised activate can
    # actually reach _open() (the entry side-effect) and not get stuck on
    # missing price/specs/budget instead.
    async def feed_576(symbol: str) -> None:
        await bus.publish("platform.account.state", {"account_id": ACCOUNT, "broker": BROKER})
        await bus.publish("market.symbol_specs", {"account_id": ACCOUNT, "symbols": [{
            "symbol": symbol, "tick_value": 1.0, "tick_size": 1.0}]})
        await bus.publish("dial.profile.state", {"profiles": [{
            "account_id": ACCOUNT, "symbol": symbol, "stop_distance_frac": 0.0055}]})
        await bus.publish("market_data.candle_closed", {
            "account_id": ACCOUNT, "symbol": symbol, "close": 64000.0})
        await bus.publish("risk.asset_ledger.state", {"ledgers": [{
            "account_id": ACCOUNT, "symbol": symbol, "risk_budget": 100.0}]})

    # T6 — no authority field at all => rejected, and NOT ONE order requested.
    await feed_576("NOAUTH")
    await bus.publish("perpetual.asset.activate", {
        "account_id": ACCOUNT, "broker": BROKER, "symbol": "NOAUTH"})
    rejected6 = bus.events_where("perpetual.entry.rejected", symbol="NOAUTH")
    orders6 = bus.events_where("execution.order.requested", symbol="NOAUTH")
    check("T6 activate w/o parent_decision_id/owner_command_id -> rejected, zero orders",
          bool(rejected6) and rejected6[-1].get("reason") == "NO_PARENT_AUTHORITY"
          and not orders6,
          "rejected=%s orders=%s" % (rejected6, orders6))

    # T7 — owner-command authority (as 901 actually stamps it: field "command_id"
    # on the wire, normalised to owner_command_id downstream) opens the pair.
    await feed_576("OWNERCMD")
    await bus.publish("perpetual.asset.activate", {
        "account_id": ACCOUNT, "broker": BROKER, "symbol": "OWNERCMD",
        "command_id": "901-cmd-42"})
    orders7 = bus.events_where("execution.order.requested", symbol="OWNERCMD")
    check("T7 activate w/ owner command_id -> execution.order.requested carries owner_command_id",
          len(orders7) == 2
          and all(o.get("owner_command_id") == "901-cmd-42" for o in orders7),
          "orders=%s" % orders7)

    # T8 — decision-parent authority opens the pair too, carrying parent_decision_id.
    # Unit 1 (2026-08-23): the declared parent must ALSO be a decision the gate
    # actually published -- so a real 467-shaped gate.passed is fed first.
    await bus.publish("decision.gate.passed", {
        "account_id": ACCOUNT, "symbol": "PARENTDEC", "cycle_id": "cyc-pd",
        "decision_id": "dec:test-581", "gate_request_id": "dec:test-581:req1",
        "decision_side": "buy", "approved": True})
    await feed_576("PARENTDEC")
    await bus.publish("perpetual.asset.activate", {
        "account_id": ACCOUNT, "broker": BROKER, "symbol": "PARENTDEC",
        "parent_decision_id": "dec:test-581"})
    orders8 = bus.events_where("execution.order.requested", symbol="PARENTDEC")
    check("T8 gate-passed parent_decision_id -> execution.order.requested carries it",
          len(orders8) == 2
          and all(o.get("parent_decision_id") == "dec:test-581" for o in orders8),
          "orders=%s" % orders8)

    # T11 — a FORGED parent_decision_id that never crossed the gate is rejected
    # loudly (DECISION_NOT_IN_GATE_WINDOW) and emits not one order.
    await feed_576("FORGEDEC")
    await bus.publish("perpetual.asset.activate", {
        "account_id": ACCOUNT, "broker": BROKER, "symbol": "FORGEDEC",
        "parent_decision_id": "dec:never-passed"})
    rejected11 = bus.events_where("perpetual.entry.rejected", symbol="FORGEDEC")
    orders11 = bus.events_where("execution.order.requested", symbol="FORGEDEC")
    check("T11 forged parent_decision_id (never gated) -> rejected, zero orders",
          bool(rejected11) and rejected11[-1].get("reason") == "DECISION_NOT_IN_GATE_WINDOW"
          and not orders11,
          "rejected=%s orders=%s" % (rejected11, orders11))

    # ------------------------------------------------------------------
    print("== 581: raw ungated decision cannot buy the actual entry a side ==")

    await bus.publish("risk.asset_ledger.state", {"ledgers": [{
        "account_id": ACCOUNT, "symbol": "SYM581", "risk_budget": 100.0}]})

    # T9 — decision.resolved.state alone (no gate) is forced to WAIT inside 581.
    await bus.publish("decision.resolved.state", {
        "account_id": ACCOUNT, "symbol": "SYM581", "cycle_id": "cyc-581",
        "direction": "buy", "strength": 0.8})
    targets9 = bus.events_where("perpetual.target.state", symbol="SYM581")
    check("T9 ungated decision.resolved.state -> perpetual target stays wait",
          bool(targets9) and targets9[-1].get("direction") == "wait",
          "last=%s" % (targets9[-1] if targets9 else None))

    # T10 — only a real 467-shaped decision.gate.passed flips the side.
    await bus.publish("decision.gate.passed", {
        "account_id": ACCOUNT, "symbol": "SYM581", "cycle_id": "cyc-581",
        "decision_id": "dec:test-581", "gate_request_id": "dec:test-581:req1",
        "direction": "buy", "strength": 0.8})
    targets10 = bus.events_where("perpetual.target.state", symbol="SYM581")
    check("T10 real decision.gate.passed -> perpetual target becomes buy",
          bool(targets10) and targets10[-1].get("direction") == "buy"
          and targets10[-1].get("gate_request_id") == "dec:test-581:req1",
          "last=%s" % (targets10[-1] if targets10 else None))

    print()
    print("failures=%d/%d" % (len(failures), 11))
    if failures:
        print("FAILING CASES: %s" % ", ".join(failures))
    else:
        print("OK: no side path found around 467 (approval/identity/dup) or 576"
              " (parent/owner authority, gate-verified since unit 1); 581 needs a"
              " real gate-pass to take a side.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run()))
