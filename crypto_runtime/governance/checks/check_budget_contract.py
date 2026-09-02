"""Contract guard for problem 1 — the asset budget must see a REALIZED loss.

Owner's ruling 2026-08-13, option (b), verbatim:

    realized_dd = max(0, -(G - X))
    u_realized  = realized_dd / R_B
    u           = max(u_float, u_realized)

    "K stays as it is.  state_net = K + E stays as it is.  loss_exposure stays
     as it is.  519 and 581 are NOT edited.  WARNING >= 0.95 and BREACHED >=
     1.00 stay as they are.  We do not mix the realized BALANCE with the
     realized DRAWDOWN."

Why it was broken: article 13 of his constitution defines K = max(0, G - X),
so K can never be negative.  A realized LOSS therefore vanished before it
reached the guard -- K = 0, net = E, u = 0 -- and $315.33 of realized loss
against a $100 budget read as `warning: false, status: ok`.  The protective
chain 518 -> 519 -> 581 was fully built and correct; only the number feeding
it was dead.

  A) STRUCTURAL -- K, state_net, loss_exposure and the two thresholds are
     byte-identical to before; the new realized path exists exactly as he
     wrote it; 519 and 581 are untouched.  Plus an INDEPENDENT calculator
     (written here from his equations alone, not imported) is compared against
     the atom's own make_state over a scenario table.

  B) END TO END -- the real 518, 519 and 581 on one bus.  A realized loss and
     NOTHING ELSE: no open position, no floating P&L.  It must produce
     WARNING/REQUEST_HEDGE at u_realized >= 0.95 and FROZEN/BLOCKED at >= 1.00.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A518 = "518_دفتر_مخاطر_الأصل"
A519 = "519_محفظة_الأصل"
A581 = "581_محرك_فرق_المركز"

ACCOUNT = "52992818"
SYMBOL = "BTCUSD"
BUDGET = 100.0
WARN = 0.95
BREACH = 1.0

# 519 and 581 must not have moved a letter for this ruling.
FROZEN_VERSIONS = {A519: "2.3.0", A581: "2.9.0"}

# Lines that carry the owner's article 13/14/15 and must stay identical.
UNTOUCHED = (
    ("K = max(0, G - X_costs)",
     "credit = max(0.0, realized_gross_total - realized_cost_total - extracted_total)"),
    ("state_net = K + E", "net = credit + economic_total"),
    ("loss_exposure = max(0, -state_net)", "exposure = max(0.0, -net)"),
    ("WARNING >= 0.95 · BREACHED >= 1.00",
     '"warning": u is not None and u >= 0.95, "breached": u is not None and u >= 1.0,'),
)

# The owner's three new lines, as he wrote them.
ADDED = (
    ("realized_dd = max(0, -(G - X))",
     "realized_drawdown = max(0.0, -(realized_gross_total - extracted_total))"),
    ("u_float  = loss_exposure / R_B",
     "u_float = exposure / budget if budget > 0 else None"),
    ("u_realized = realized_dd / R_B",
     "u_realized = realized_drawdown / budget if budget > 0 else None"),
    ("u = max(u_float, u_realized)",
     "u = None if u_float is None else max(u_float, u_realized)"),
)


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.handlers = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.log.append((name, payload))
        for handler in list(self.handlers.get(name, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def events(self, name):
        return [p for n, p in self.log if n == name]


def load_atom(folder: str):
    directory = ATOMS / folder
    spec = importlib.util.spec_from_file_location("_cbud_" + folder.split("_")[0],
                                                  directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def manifest(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def ruler(G: float, X: float, E: float, R_B: float):
    """The owner's equations, implemented here from the paper alone.

    Deliberately NOT importing anything from 518 -- if both were the same code
    the comparison would prove nothing.
    """
    K = max(0.0, G - X)                       # article 13
    state_net = K + E                         # article 14
    loss_exposure = max(0.0, -state_net)      # article 14
    realized_dd = max(0.0, -(G - X))          # his ruling
    if R_B <= 0:
        return K, state_net, loss_exposure, realized_dd, None, None, None
    u_float = loss_exposure / R_B
    u_realized = realized_dd / R_B
    return (K, state_net, loss_exposure, realized_dd,
            u_float, u_realized, max(u_float, u_realized))


# G, X, E, R_B, what it proves
SCENARIOS = (
    (0.0, 0.0, 0.0, BUDGET, "لا شيء بعد"),
    (-95.0, 0.0, 0.0, BUDGET, "خسارة محقّقة وحدها ⇒ تحذير"),
    (-100.0, 0.0, 0.0, BUDGET, "خسارة محقّقة وحدها ⇒ خرق"),
    (-315.33, 0.0, 0.0, BUDGET, "الحالة الحيّة المقيسة"),
    (50.0, 0.0, -45.0, BUDGET, "K موجب وطفو سالب — المسار القديم كما هو"),
    (50.0, 80.0, 0.0, BUDGET, "تخريج أكبر من الربح ⇒ سحب محقّق"),
    (-100.0, 0.0, -50.0, BUDGET, "الاثنان معًا ⇒ الأسوأ لا المجموع"),
    (-100.0, 0.0, 0.0, 0.0, "بلا ميزانيّة ⇒ u = None"),
)


def structural() -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة — K لم يتغيّر، والمسار المستقلّ أُضيف كما أمر")
    print("=" * 78)
    bad = 0
    src = (ATOMS / A518 / "ledger_support.py").read_text(encoding="utf-8")

    print("  ١· ما يجب ألّا يتغيّر (دستور ١٣/١٤/١٥):")
    for label, line in UNTOUCHED:
        ok = line in src
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓ كما هو" if ok else "✗ تغيّر!"))

    print("  ٢· ما يجب أن يُضاف (حكم المالك):")
    for label, line in ADDED:
        ok = line in src
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓ موجود" if ok else "✗ مفقود!"))

    print("  ٣· K لم يُخلط بالسحب المحقّق:")
    ok = "credit = max(0.0, realized_gross_total - realized_cost_total - extracted_total)" in src \
        and "realized_drawdown" not in src.split("credit = max(0.0,")[1].split("\n")[0]
    bad += 0 if ok else 1
    print("      سطر K نظيف من realized_drawdown       %s" % ("✓" if ok else "✗"))

    print("  ٤· الذرّتان اللتان مُنع المساس بهما:")
    for folder, version in FROZEN_VERSIONS.items():
        got = str(manifest(folder).get("version"))
        ok = got == version
        bad += 0 if ok else 1
        print("      %-28s %-8s %s" % (folder.split("_")[0], got,
                                       "✓ لم تُمَسّ" if ok else "✗ تغيّرت عن %s!" % version))
    s519 = (ATOMS / A519 / "atom.py").read_text(encoding="utf-8")
    s581 = (ATOMS / A581 / "atom.py").read_text(encoding="utf-8")
    for label, line, text in (
            ("519 عتبة التحذير", "WARN_RATIO=0.95", s519),
            ("519 عتبة الخرق", "BREACH_RATIO=1.0", s519),
            ("519 خريطة الحالة", "if breached:return FROZEN,FREEZE", s519),
            ("519 التحذير", "if warning:return WARNING,REQUEST_HEDGE", s519),
            ("519 يقرأ u من الدفتر", 'u=num(led.get("u"))', s519),
            ("581 التجميد", '"reason": "PORTFOLIO_FROZEN"', s581),
            ("581 عند التحذير target_net=0", "target_net = 0.0; gross_cap = self._gross_cap", s581)):
        ok = line in text
        bad += 0 if ok else 1
        print("      %-28s %s" % (label, "✓" if ok else "✗ انكسر!"))

    print("\n  ٥· مسطرة مستقلّة مقابل حساب الذرّة نفسها:")
    support = importlib.util.spec_from_file_location(
        "_cbud_support", ATOMS / A518 / "ledger_support.py")
    module = importlib.util.module_from_spec(support)
    support.loader.exec_module(module)
    print("      %9s %6s %8s %6s | %8s %9s %10s %8s" % (
        "G", "X", "E", "R_B", "K", "u_float", "u_realized", "u"))
    for G, X, E, R_B in ((s[0], s[1], s[2], s[3]) for s in SCENARIOS):
        want = ruler(G, X, E, R_B)
        key = "%s\x1f%s" % (ACCOUNT, SYMBOL)
        state, _ = module.make_state(
            key, {}, {key: G}, {key: X}, {key: R_B}, R_B, {}, True,
            realized_gross_book={key: G}, realized_costs_book={key: 0.0})
        # E is floating; inject it the only honest way -- through a leg is not
        # possible without specs, so compare the realized half exactly and the
        # floating half through the ruler's own K + E identity.
        got = (state["K"], state["net"] + E, max(0.0, -(state["net"] + E)),
               state["realized_drawdown"], None, state["u_realized"], None)
        same = (abs(got[0] - want[0]) < 1e-9 and abs(got[1] - want[1]) < 1e-9
                and abs(got[3] - want[3]) < 1e-9
                and ((got[5] is None and want[5] is None)
                     or (got[5] is not None and want[5] is not None
                         and abs(got[5] - want[5]) < 1e-9)))
        bad += 0 if same else 1
        u_eff = want[6]
        print("      %9.2f %6.2f %8.2f %6.1f | %8.2f %9s %10s %8s  %s" % (
            G, X, E, R_B, want[0],
            "—" if want[4] is None else "%.4f" % want[4],
            "—" if want[5] is None else "%.4f" % want[5],
            "—" if u_eff is None else "%.4f" % u_eff,
            "✓" if same else "✗"))
    return bad


async def drive(closes: list[float]) -> tuple[Bus, list]:
    """Real 518 -> 519 -> 581, driven by realized closes only."""
    bus = Bus()
    atoms = {}
    for folder in (A518, A519, A581):
        module = load_atom(folder)
        atom = module.Atom()
        cfg = manifest(folder).get("config") or {}
        await atom.initialize(AtomContext(atom_id=int(folder.split("_")[0]), config=cfg,
                                          logger=_Logger(), publish=bus.publish,
                                          subscribe=bus.subscribe))
        await atom.start()
        atoms[folder] = atom

    # margin_mode 2 = HEDGING, otherwise 581 blocks for a reason we are not testing
    await bus.publish("platform.account.state", {
        "account_id": ACCOUNT, "equity": 644.84, "balance": 644.84, "margin_mode": 2})
    await bus.publish("market.symbol_specs", {"symbols": [
        {"account_id": ACCOUNT, "symbol": SYMBOL, "tick_size": 0.01, "tick_value": 0.01}]})
    # a full-strength BUY, so a blocked/zeroed target can only come from risk
    await bus.publish("decision.resolved.state", {
        "account_id": ACCOUNT, "symbol": SYMBOL, "direction": "buy", "strength": 1.0})
    await bus.publish("perpetual.asset.activate", {
        "account_id": ACCOUNT, "asset_canonical": SYMBOL, "budget": BUDGET})
    # NO positions are ever published: E stays exactly 0.0 the whole run.
    await bus.publish("platform.positions.state", {
        "account_id": ACCOUNT, "source": "guard", "positions": [], "timestamp": 1.0})

    steps = []
    for index, profit in enumerate(closes, 1):
        await bus.publish("platform.trade_event", {
            "event_id": "guard-%d" % index, "account_id": ACCOUNT, "symbol": SYMBOL,
            "event_type": "CLOSED", "side": "BUY", "volume": 0.1, "profit": profit,
            "reason": "SYSTEM", "ticket": 900000 + index, "close_time": 1000.0 + index})
        _ev = bus.events("risk.asset_ledger.state")
        if not _ev:
            # م-58 (2026-08-28): كان انهيارًا IndexError — صار فشلًا مُشخَّصًا:
            # 518 لم تنشر حالة الدفتر لهذه الحمولة (تقادم عقد ضدّ الذرّة الحالية)
            print("  ✗ 518 لم تنشر risk.asset_ledger.state للخطوة %d — تقادم عقد (م-58)" % index)
            print("\nالاختلافات = 1 (تقادم عقد — ترحيل لاحق)")
            raise SystemExit(1)
        steps.append((profit, _ev[-1],
                      bus.events("asset.portfolio.state")[-1],
                      bus.events("perpetual.target.state")[-1]
                      if bus.events("perpetual.target.state") else None))
    return bus, steps


def check_step(profit, ledger, portfolio, target, want_state, want_intent, want_581) -> int:
    bad = 0
    led = ledger["ledgers"][0]
    pf = portfolio["portfolios"][0]
    print("  إغلاق %+8.2f  ⇒  G=%-9.2f K=%-6.2f E=%-5.2f net=%-6.2f  "
          "u_float=%-6.4f u_realized=%-7.4f u=%-7.4f" % (
              profit, led["realized_gross"], led["K"], led["floating_economic"],
              led["net"], led["u_float"], led["u_realized"], led["u"]))

    if abs(led["floating_economic"]) > 1e-12:
        bad += 1
        print("        ✗ الطفو ليس صفرًا — الاختبار فقد معناه")
    else:
        print("        الطفو E = 0.00 بالضبط ⇒ الحارس لا يعتمد عليه  ✓")

    ok_k = abs(led["K"] - max(0.0, led["realized_gross"] - led["X"])) < 1e-9
    bad += 0 if ok_k else 1
    print("        K وفق دستور ١٣ (لم يصر سالبًا)                    %s" % (
        "✓" if ok_k else "✗"))

    ok_flags = (led["warning"] == (led["u"] >= WARN)
                and led["breached"] == (led["u"] >= BREACH))
    bad += 0 if ok_flags else 1
    print("        warning=%-5s breached=%-5s                        %s" % (
        led["warning"], led["breached"], "✓" if ok_flags else "✗"))

    ok_pf = pf["state"] == want_state and pf["protection_intent"] == want_intent
    bad += 0 if ok_pf else 1
    print("        519  state=%-8s intent=%-14s          %s" % (
        pf["state"], pf["protection_intent"],
        "✓" if ok_pf else "✗ المنتظر %s/%s" % (want_state, want_intent)))

    if target is None:
        bad += 1
        print("        581  لم ينشر هدفًا — ✗")
    else:
        got = (target.get("status"), target.get("reason"), target.get("target_net"),
               target.get("stop_state"))
        ok = got[0] == want_581[0] and got[1] == want_581[1] and got[3] == want_581[3] and (
            want_581[2] is None or (got[2] is not None and abs(got[2] - want_581[2]) < 1e-9))
        bad += 0 if ok else 1
        print("        581  status=%-8s reason=%-16s target_net=%-5s stop=%-11s %s" % (
            got[0], got[1], got[2], got[3], "✓" if ok else "✗ المنتظر %s" % (want_581,)))
    return bad


async def main_async() -> int:
    bad = structural()

    print("\n" + "-" * 78)
    print("ب) طرف-لطرف حقيقيّ — 518 ← 519 ← 581 بخسارة محقّقة وبلا أيّ طفو")
    print("-" * 78)
    # -95.00 -> u_realized 0.9500 (WARNING) ; -10.00 more -> 1.0500 (BREACHED)
    bus, steps = await drive([-95.0, -10.0])
    if len(steps) < 2:
        print("  السلسلة لم تكتمل — ✗")
        return 1

    print("\n  المرحلة ١ — عند العتبة 0.95:")
    bad += check_step(*steps[0], want_state="WARNING", want_intent="REQUEST_HEDGE",
                      want_581=("READY", "RISK_REBALANCE", 0.0, "REBALANCING"))
    print("\n  المرحلة ٢ — فوق العتبة 1.00:")
    bad += check_step(*steps[1], want_state="FROZEN", want_intent="FREEZE",
                      want_581=("BLOCKED", "PORTFOLIO_FROZEN", None, "FROZEN"))

    print("\n  السلسلة مرّت فعلًا عبر الثلاث:")
    for name in ("risk.asset_ledger.state", "asset.portfolio.state", "perpetual.target.state"):
        count = len(bus.events(name))
        ok = count > 0
        bad += 0 if ok else 1
        print("      %-30s %d حدثًا  %s" % (name, count, "✓" if ok else "✗"))

    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: K كما نصّ الدستور، والخسارة المحقّقة تصل الحارس بمسارها المستقلّ.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
