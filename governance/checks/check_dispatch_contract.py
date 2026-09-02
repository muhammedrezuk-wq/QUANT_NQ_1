"""Contract guard for problem 4 — one decision, one execution path.

Owner's ruling 2026-08-13, option (a), verbatim:

    "467 stays as a transform/diagnostic layer, but STOPS publishing
     execution.order.requested.  581 becomes the single decision point that
     produces the real execution target.  The verdict of the filters
     458 -> 454 -> 466 must reach 581 as an EXPLICIT input.  We do not treat
     the missing fields in 467 as a safety barrier -- precedence must be an
     explicit contract.  Do not change the weights, the budget, the stop,
     `enabled`, or 581's internal logic beyond receiving that verdict."

Why: 467 owns no decision logic at all -- it re-shapes decision.approved.state
and emits an order that can bypass 581.  Measured live, in ONE cycle and under
ONE trace_id, 581 said `action=HOLD, target_net=0.0` while 467 dispatched a
SELL three milliseconds later.  Nothing but three missing fields
(`account_id`, `volume`, and 513's null lots) stopped it from reaching the
broker, and none of those is a rule.

  A) STRUCTURAL -- 467 no longer declares or writes the execution event and is
     still alive as a layer; the only declared publishers of that event are the
     perpetual pair (578) and the owner's activation path (576); 581 declares
     and reads the filter verdict; 581's own hedge contract is untouched; the
     gates are still shut.

  B) END TO END -- the real 460 -> 454 -> 466 -> 467 / 581 -> 583 -> 578.  A
     decision the confidence filter BLOCKS must not reach execution by ANY
     path; the same decision, unblocked, must go through 581 and only 581.

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
A454 = "454_فلتر_القرار"
A460 = "460_فلتر_الثقة"
A466 = "466_موافقة_القرار"
A467 = "467_إرسال_القرار"
A578 = "578_منفذ_التحوط"
A581 = "581_محرك_فرق_المركز"
A583 = "583_لقطة_التنفيذ"

EVENT_EXEC = "execution.order.requested"
ACCOUNT = "52992818"
SYMBOL = "BTCUSD"
TF = "60s"
CYCLE = "%s|%s|1000.0" % (SYMBOL, TF)
PRICE = 63478.22
BUDGET = 100.0

# 576 = the owner's activation path (901 -> perpetual.asset.activate), not a
# decision. 578 = the only difference executor, and it is fed by 581 alone.
ALLOWED_PUBLISHERS = {"576", "578"}


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
    spec = importlib.util.spec_from_file_location("_cdisp_" + folder.split("_")[0],
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


def code_only(source: str) -> str:
    """Executable source with comments stripped — same spirit as المادة 9."""
    out = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  #", 1)[0])
    return "\n".join(out)


def structural() -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة — مصدر قرار واحد لمسار التنفيذ")
    print("=" * 78)
    bad = 0

    m467 = manifest(A467)
    s467 = (ATOMS / A467 / "atom.py").read_text(encoding="utf-8")
    published = list(m467.get("publishes") or [])
    ok = EVENT_EXEC not in published
    bad += 0 if ok else 1
    print("  467 لا يعلن حدث التنفيذ                    : %s %s" % (
        "✓" if ok else "✗ ما زال يعلنه!", published))
    # التعليق الذي يشرح التغيير يذكر اسم الحدث بالضرورة؛ الممنوع أن يبقى بالكود
    # المنفَّذ، فنجرّد التعليقات قبل الحكم بدل أن نمنع توثيق ما جرى.
    ok = EVENT_EXEC not in code_only(s467)
    bad += 0 if ok else 1
    print("  467 لا يكتبه بالكود المنفَّذ                : %s" % ("✓" if ok else "✗"))
    ok = bool(published)
    bad += 0 if ok else 1
    print("  467 ما زال حيًّا طبقةً (لم يُحذف)            : %s" % ("✓" if ok else "✗ صار أخرس"))

    publishers = set()
    for folder in sorted(p.name for p in ATOMS.iterdir() if p.is_dir()):
        path = ATOMS / folder / "manifest.yaml"
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if EVENT_EXEC in (data.get("publishes") or []):
            publishers.add(str(data.get("id")))
    ok = publishers == ALLOWED_PUBLISHERS
    bad += 0 if ok else 1
    print("  ناشرو حدث التنفيذ بالمشروع كلّه            : %s %s" % (
        "✓" if ok else "✗ المنتظر %s" % sorted(ALLOWED_PUBLISHERS), sorted(publishers)))

    m581 = manifest(A581)
    s581 = (ATOMS / A581 / "atom.py").read_text(encoding="utf-8")
    ok = "decision.approved.state" in (m581.get("subscribes") or [])
    bad += 0 if ok else 1
    print("  581 يعلن حكم الفلاتر مدخلًا                : %s" % ("✓" if ok else "✗ غائب"))
    ok = "decision.approved.state" in s581 and "_on_verdict" in s581
    bad += 0 if ok else 1
    print("  581 يقرأه فعلًا بالكود                     : %s" % ("✓" if ok else "✗"))
    ok = "FILTER_BLOCKED" in s581
    bad += 0 if ok else 1
    print("  581 يعلن سبب الحجب بمخرَجه                 : %s" % ("✓" if ok else "✗"))

    print("  وعقد 581 الداخليّ لم يُمَسّ:")
    cfg = m581["config"]
    for label, ok in (
            # البند ٦ (حكمه 2026-08-15): نطاق `≥0.90` مشطوب من الجدولين — غير
            # قابل للوصول بالبناء (الإجماع المقيس 0.8256 على مقام 6.0564).
            # الحاجز يبقى «لم يُمَسّ»؛ مرجعه وحده تحرّك، وبسبب مكتوب.
            ("جدول E(S)", cfg["bands"] == {"0.0": 0.0, "0.2": 0.1, "0.4": 0.25,
                                            "0.6": 0.5}),
            ("جدول H(S)", cfg["hedge_bands"] == {"0.0": 1.0, "0.2": 0.7, "0.4": 0.4,
                                                  "0.6": 0.2}),
            ("s_enter / s_exit", (cfg["s_enter"], cfg["s_exit"]) == (0.20, 0.15)),
            ("معادلتا الكسر", "_fraction" in s581 and "_hedge_fraction" in s581),
            ("عقد التعرّض", "NEUTRAL_KEEP_GROSS" in s581)):
        bad += 0 if ok else 1
        print("      %-28s %s" % (label, "✓" if ok else "✗ تغيّر!"))

    # حكم المالك ٢٠٢٦-٠٨-١٥، الخيار (أ): الحارس يعزل حالة البوّابة داخل محكّه.
    # هذا الحارس لا يُدخل `552` في سلسلته أصلًا (يتوقّف عند `578`)، فحالة
    # البوّابة تُسجَّل للعلم ولا تُشترَط — وأمرُ السوق يُقاس بغيابه لا بإعدادها.
    for folder, name in (("552_مدقق_الأمر", "552"), ("575_مرسل_الإدارة", "575")):
        enabled = manifest(folder)["config"].get("enabled")
        print("  حالة بوّابة %-4s (تُسجَّل ولا تُشترَط)      : enabled=%s" % (name, enabled))
    return bad


async def drive(confidence_signal: str, decision_cycle: str = CYCLE,
                with_approval: bool = True) -> Bus:
    """The real chain: 460 -> 454 -> 466 -> 467 · and 458-shaped input -> 581 -> 583 -> 578.

    with_approval=False drops 466 from the bus, so no verdict ever reaches 581 --
    the fail-closed default the precedence contract rests on.
    """
    bus = Bus()
    chain = (A460, A454, A466, A467, A581, A583, A578) if with_approval else (
        A460, A454, A581, A583, A578)
    for folder in chain:
        module = load_atom(folder)
        atom = module.Atom()
        await atom.initialize(AtomContext(atom_id=int(folder.split("_")[0]),
                                          config=manifest(folder).get("config") or {},
                                          logger=_Logger(), publish=bus.publish,
                                          subscribe=bus.subscribe))
        await atom.start()

    await bus.publish("market.symbol_specs", {"symbols": [
        {"account_id": ACCOUNT, "symbol": SYMBOL, "tick_size": 1.0, "tick_value": 1.0,
         "point": 0.01, "stops_level": 0.0, "volume_step": 0.01, "volume_min": 0.01}]})
    await bus.publish("platform.account.state", {
        "account_id": ACCOUNT, "trade_allowed": True, "equity": 644.84,
        "free_margin": 644.84, "leverage": 5000, "margin_mode": 2})
    await bus.publish("SYS_SECOND", {"official_time": 1000.0})
    await bus.publish("feed.mt5.tick", {"symbol": SYMBOL, "price": PRICE})
    await bus.publish("risk.asset_ledger.state", {"ledgers": [{
        "account_id": ACCOUNT, "symbol": SYMBOL, "asset_canonical": SYMBOL,
        "risk_budget": BUDGET, "R": BUDGET, "budget": BUDGET, "budgeted": True,
        "K": 0.0, "buffer_k": 0.0, "cost": 0.0, "commission_est": 0.0,
        "v_net": 0.0, "w": 0.0, "vpu": 1.0, "u": 0.0}], "count": 1})
    await bus.publish("asset.portfolio.state", {"portfolios": [{
        "account_id": ACCOUNT, "symbol": SYMBOL, "state": "NORMAL",
        "protection_intent": "NONE", "v_net": 0.0, "account_mode": "HEDGING"}],
        "count": 1, "halted": False})
    await bus.publish("dial.profile.state", {"profiles": [{
        "account_id": ACCOUNT, "symbol": SYMBOL, "stop_distance_frac": 0.0055}]})

    # the filter verdict must be cached BEFORE the decision, exactly as live
    await bus.publish("probability.confidence.state", {
        "symbol": SYMBOL, "timeframe": TF, "cycle_id": CYCLE, "signal": confidence_signal})
    await bus.publish("decision.resolved.state", {
        "symbol": SYMBOL, "account_id": ACCOUNT, "timeframe": TF, "cycle_id": decision_cycle,
        "id": "conflict_resolver", "status": "ok", "signal": "sell", "direction": "sell",
        "score": 100.0, "confidence": 0.9, "strength": 0.9, "reason": "RESOLVED"})
    return bus


def report(bus: Bus, label: str, must_execute: bool, want_filter: bool = None,
           want_verdict: str = None) -> int:
    bad = 0
    print("  %s" % label)
    filtered = bus.events("decision.filtered.state")
    approved = bus.events("decision.approved.state")
    passed = bool(filtered) and bool(filtered[-1]["metadata"].get("passed"))
    ok_approved = (bool(approved) and bool(approved[-1]["metadata"].get("approved"))) if approved else "—"
    print("      454 passed=%-6s blocked_by=%-22s · 466 approved=%s" % (
        passed, filtered[-1]["metadata"].get("blocked_by") if filtered else "—", ok_approved))
    if passed != (must_execute if want_filter is None else want_filter):
        bad += 1
        print("      ✗ الفلتر لم يتصرّف كما هو منتظر")

    dispatched = [p for p in bus.events(EVENT_EXEC) if p.get("source") == "decision_dispatch"]
    bad += 0 if not dispatched else 1
    print("      467 → حدث تنفيذ: %-3d %s" % (
        len(dispatched), "✓ لا شيء" if not dispatched else "🔴 ما زال يلتفّ!"))

    targets = bus.events("perpetual.target.state")
    if not targets:
        print("      581 لم ينشر هدفًا — ✗")
        return bad + 1
    target = targets[-1]
    print("      581 → action=%-8s target_net=%-10s delta_buy=%-6s delta_sell=%-6s verdict=%s" % (
        target.get("action"), target.get("target_net"), target.get("delta_buy"),
        target.get("delta_sell"), target.get("filter_verdict")))

    orders = [p for p in bus.events(EVENT_EXEC) if p.get("origin") == "perpetual-delta"]
    if must_execute:
        checks = (("581 أعطى اتجاهًا", target.get("held_direction") in ("buy", "sell")),
                  ("581 أعطى فرقًا", abs(float(target.get("delta_sell") or 0.0))
                   + abs(float(target.get("delta_buy") or 0.0)) > 0.0),
                  ("578 أرسل عبر 581 وحده", len(orders) >= 1),
                  ("والأمر يحمل ستوبًا", bool(orders) and (orders[-1].get("stop_loss") or 0) > 0))
    else:
        checks = (("581 بلا اتجاه", target.get("held_direction") in (None, "wait")),
                  ("الصافي صفر", abs(float(target.get("target_net") or 0.0)) < 1e-12),
                  ("لا فرق يُرسَل", abs(float(target.get("delta_sell") or 0.0))
                   + abs(float(target.get("delta_buy") or 0.0)) < 1e-12),
                  ("الإجماليّ محفوظ لا مصفّى", target.get("reason") != "CLOSE_ALL"),
                  ("578 لم يرسل شيئًا", not orders),
                  ("581 يعلن سبب المنع",
                   target.get("filter_verdict") == (want_verdict or "FILTER_BLOCKED")))
    for name, ok in checks:
        bad += 0 if ok else 1
        print("      %-32s %s" % (name, "✓" if ok else "✗"))
    return bad


async def main_async() -> int:
    # شرطاه ٤ و٥: الفحص لا يكتب بايتًا في بطاقتَي البوّابة، ولو سقط في منتصفه.
    cards = {folder: (ATOMS / folder / "manifest.yaml").read_bytes()
             for folder in ("552_مدقق_الأمر", "575_مرسل_الإدارة")}
    try:
        return await _run()
    finally:
        for folder, raw in cards.items():
            same = (ATOMS / folder / "manifest.yaml").read_bytes() == raw
            print("      بطاقة %-6s لم تُكتب %s" % (folder.split("_")[0],
                                                   "✓" if same else "🔴 تغيّرت!"))


async def _run() -> int:
    bad = structural()
    print("\n" + "-" * 78)
    print("ب) طرف-لطرف حقيقيّ — 460 ← 454 ← 466 ← 467 · و581 ← 583 ← 578")
    print("-" * 78)
    print()
    bad += report(await drive("low_confidence"),
                  "١· الفلتر يحجب القرار — لا يجوز أن يصل التنفيذ بأيّ طريق", False)
    print()
    bad += report(await drive("high_confidence"),
                  "٢· الفلتر يسمح — التنفيذ يمرّ عبر 581 وحده", True)
    print()
    # الأسبقيّة عقد صريح لا صدفة: قرار بلا حكم مطابق لدورته لا يفتح شيئًا،
    # حتى لو كان الفلتر قد سمح لدورة أخرى. مغلق افتراضًا.
    bad += report(await drive("high_confidence", with_approval=False),
                  "٣· الحكم لم يصل أصلًا — مغلق افتراضًا", False,
                  want_filter=True, want_verdict="FILTER_PENDING")

    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: مصدر قرار واحد، وحكم الفلاتر يصل 581 صراحةً لا صدفةً.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
