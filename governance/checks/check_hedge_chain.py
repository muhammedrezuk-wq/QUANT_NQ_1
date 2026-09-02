"""Phases D+E of the hedge-contract proof: the REAL atoms wired together
on a routing bus — 581 -> 583 -> 578 -> 586/708 -> 585 -> 516 -> 551 -> 584
-> 552(enabled=false). The target born from the decision must arrive at the
execution gate as a PREVIEW, with the same numbers, and NOTHING may head to
the platform: no trading.final_decision, no bridge signal. Exit 1 otherwise.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)
sys.path.insert(0, str(ROOT))

from core.contracts.atom import AtomContext  # noqa: E402

SYMBOL = "BTCUSD"
ACCOUNT = "52992818"
PRICE = 64000.0
STOP_FRAC = 0.0055
FORBIDDEN = ("trading.final_decision", "platform.brain_signal.written")


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
    directory = ATOM_ROOT / folder
    spec = importlib.util.spec_from_file_location("_chain_" + folder.split("_")[0],
                                                  directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


CONFIGS = {
    "581_محرك_فرق_المركز": {
        # Owner's FINAL table (2026-08-13): E allows the exposure, H hedges part
        # of it away and FALLS as conviction rises. Net = Capacity x E x (1-H).
        # حكم المالك ٢٠٢٦-٠٨-١٥ (البند ٦): نطاق ≥0.90 مشطوب — نفس سلّم البطاقة
        # المشحونة حرفيًّا، لا سلّم أوسع منها.
        "bands": {"0.0": 0.0, "0.2": 0.1, "0.4": 0.25, "0.6": 0.5},
        "hedge_bands": {"0.0": 1.0, "0.2": 0.7, "0.4": 0.4, "0.6": 0.2},
        "s_enter": 0.20, "s_exit": 0.15, "max_target_volume": 20,
        "max_step_volume": 1, "min_volume": 0.01, "hedge_cost_per_volume": 0.0},
    "583_لقطة_التنفيذ": {},
    # البند ٢ (الخيار ج): الملاذ الأخير ×3 من مسافة الميزانيّة، وبديل معلَن
    # عند v_net = 0. المفتاحان إلزاميّان بالمنيفست فيلزمان هنا أيضًا.
    "578_منفذ_التحوط": {"lot_step": 0.01, "min_volume": 0.01, "reward_risk": 2.0,
                          "max_attempts": 3, "resend_hold_s": 2.0,
                          "catastrophe_stop_multiple": 3.0, "fallback_stop_frac": 0.02},
    "586_بوابة_الرموز": {},
    "708_سجل_الرموز": {"canonical_map": {"BTCUSD": ["BTCUSD"]},
                         "canonical_patterns": {},
                         "broker_map": {"52992818|BTCUSD": "BTCUSD"},
                         "strip_suffixes": [], "passthrough_unknown": True,
                         "min_stem_length": 3},
    "585_حارس_الهامش": {"margin_buffer_pct": 0.1},
    "516_قاطع_الأمان": {"max_daily_loss_pct": 60, "max_consecutive_losses": 5,
                          "max_daily_trades": 200, "max_open_trades": 20},
    "551_باني_الأمر": {"reward_risk": 2.0},
    "584_شرعية_الستوب": {"stop_buffer_points": 20},
    "552_مدقق_الأمر": {"enabled": False},
}


async def run() -> int:
    bus = Bus()
    atoms = []
    for index, (folder, config) in enumerate(CONFIGS.items()):
        module = load_atom(folder)
        atom = module.Atom()
        context = AtomContext(atom_id=500 + index, config=config, logger=_Logger(),
                              publish=bus.publish, subscribe=bus.subscribe)
        await atom.initialize(context)
        await atom.start()
        atoms.append((folder, atom))

    clock = {"official_time": 1000.0}
    await bus.publish("SYS_SECOND", clock)
    # 581 تفهرس المواصفات والسعر بمفتاح (حساب، رمز) — بلا account_id لا يُخزَّن
    # شيء فتحكم MISSING_R_PRICE_DIAL_OR_SPECS على كل شيء (نفس درس فحص العقد).
    await bus.publish("market.symbol_specs", {"account_id": ACCOUNT, "symbols": [{
        "symbol": SYMBOL, "tick_value": 1.0, "tick_size": 1.0, "point": 0.01,
        "contract_size": 1.0, "volume_step": 0.01, "volume_min": 0.01,
        "volume_max": 100.0, "stops_level": 0, "freeze_level": 0}]})
    # 583 تتعلّم وسيط كل حساب من حالة الحساب (الهوية الخماسية) — بلا broker
    # هنا تسقط كل مفاتيحها بصمت فلا تصدر أي لقطة تنفيذ.
    await bus.publish("platform.account.state", {
        "account_id": ACCOUNT, "broker": "Raw Trading Ltd",
        "free_margin": 645.0, "equity": 645.0,
        "balance": 645.0, "leverage": 5000, "margin_mode": 2})
    # 516 لا يعدّ النظام سليمًا إلا باكتمال الرايات الثلاث معًا.
    await bus.publish("platform.terminal_state", {
        "account_id": ACCOUNT, "connected": True, "trade_allowed": True,
        "expert_allowed": True, "official_time": 1000.0})
    # حارس الهامش (585) لا يقرأ المال من حالة الحساب الخام — يأخذه من «الحقيقة
    # المالية» بأحداث أصحابها (654 الملاءة · 656 الهامش الحر) حصراً.
    await bus.publish("portfolio.equity.state", {
        "account_id": ACCOUNT, "broker": "Raw Trading Ltd",
        "equity": 645.0, "measured_at": 1000.0})
    await bus.publish("portfolio.free_margin.state", {
        "account_id": ACCOUNT, "broker": "Raw Trading Ltd",
        "free_margin": 645.0, "measured_at": 1000.0})
    await bus.publish("dial.profile.state", {"profiles": [{
        "account_id": ACCOUNT, "symbol": SYMBOL, "stop_distance_frac": STOP_FRAC}]})
    # ‏581 v3.1.0 تشترط إشارة حياة النظام من محفظة الأصل (519) — كما تبثّها
    # 519 حين يكون النظام حيًّا فعلًا؛ غيابها = BLOCKED SYSTEM_NOT_ALIVE للكل.
    await bus.publish("asset.portfolio.state", {"portfolios": [{
        "account_id": ACCOUNT, "symbol": SYMBOL, "state": "NORMAL",
        "system_alive": True, "account_mode": "HEDGING"}]})
    # 578 يشترط صورة مراكز موسومة بالصلاحية (usable_*) ليتصالح دفتره مع
    # المنصّة، وجودة تنفيذ جاهزة، وانحراف مرجع متزامنًا — كما تبثّها 609
    # و577 و582 في النظام الحي. غيابها = حجب صامت لكل فتح.
    await bus.publish("platform.positions.state", {
        "source": "test", "account_id": ACCOUNT, "broker": "Raw Trading Ltd",
        "timestamp": 999.0, "positions": [],
        "usable_for_new_exposure": True, "usable_for_protection": True})
    await bus.publish("execution.quality.state", {
        "account_id": ACCOUNT, "broker": "Raw Trading Ltd", "symbol": SYMBOL,
        "status": "READY"})
    await bus.publish("execution.reference_divergence.state", {
        "account_id": ACCOUNT, "broker": "Raw Trading Ltd", "symbol": SYMBOL,
        "timeframe": "", "status": "SYNCED"})
    await bus.publish("risk.asset_ledger.state", {"ledgers": [{
        "account_id": ACCOUNT, "symbol": SYMBOL, "risk_budget": 100.0,
        "budgeted": True, "v_net": 0.0, "realized_gross": 0.0}]})
    await bus.publish("market_data.candle_closed", {
        "account_id": ACCOUNT, "symbol": SYMBOL, "close": PRICE, "timeframe": "60s"})
    await bus.publish("feed.mt5.tick", {
        "provider": "MT5", "account_id": ACCOUNT, "symbol": SYMBOL,
        "bid": PRICE - 6, "ask": PRICE + 6,
        "price": PRICE, "timestamp": 1000.0})

    await bus.publish("SYS_SECOND", {"official_time": 1003.0})
    # حكم المالك ٢٠٢٦-٠٨-١٣ (البند ٤): حكم الفلاتر مدخل صريح لـ581، ومغلق
    # افتراضًا. السلسلة هنا تقيس التحوّط حتى البوّابة، فيصلها الحكم صراحةً.
    await bus.publish("decision.approved.state", {
        "symbol": SYMBOL, "cycle_id": "chain-1", "metadata": {"approved": True}})
    await bus.publish("decision.resolved.state", {
        "symbol": SYMBOL, "account_id": ACCOUNT, "direction": "buy",
        "cycle_id": "chain-1", "strength": 0.75})
    await bus.publish("SYS_SECOND", {"official_time": 1006.0})

    failures = 0

    def stage(name, rows, describe):
        nonlocal failures
        if rows:
            print("  %-34s %d event(s)  %s" % (name, len(rows), describe(rows[-1])))
        else:
            print("  %-34s MISSING <-- CHAIN BROKEN" % name)
            failures += 1
        return rows

    print("chain trace:")
    targets = stage("581 perpetual.target.state", bus.events("perpetual.target.state"),
                    lambda r: "net=%s gross=%s action=%s" % (
                        r.get("target_net"), r.get("target_gross"), r.get("action")))
    stage("583 execution.snapshot.state", bus.events("execution.snapshot.state"),
          lambda r: "snapshot_id=%s" % r.get("snapshot_id"))
    requested = stage("578 execution.order.requested", bus.events("execution.order.requested"),
                      lambda r: "%s %s vol=%s" % (r.get("action"), r.get("side"), r.get("volume")))
    stage("586 execution.order.resolved", bus.events("execution.order.resolved"),
          lambda r: "broker_symbol=%s" % r.get("broker_symbol"))
    margins = stage("585 margin verdicts", bus.events("risk.margin.validation.completed"),
                    lambda r: "approved=%s reason=%s" % (r.get("approved"), r.get("reason")))
    verdicts = stage("516 risk.validation.completed", bus.events("risk.validation.completed"),
                     lambda r: "approved=%s reason=%s" % (r.get("approved"), r.get("reason")))
    stage("551 execution.order.built", bus.events("execution.order.built"),
          lambda r: "side=%s vol=%s" % (r.get("side"), r.get("volume")))
    stage("584 execution.order.legal", bus.events("execution.order.legal"),
          lambda r: "side=%s vol=%s" % (r.get("side"), r.get("volume")))
    # لا يوجد حدث منفصل execution.order.preview بكود ٥٥٢ الحالي — كل رفض
    # (بما فيه البوّابة المقفولة reason=disabled) على execution.order.rejected
    # وحده (اكتُشف ٢٠٢٦-٠٨-١٩، راجع سياق/٩٠).
    previews = stage("552 execution.order.rejected", bus.events("execution.order.rejected"),
                     lambda r: "reason=%s side=%s vol=%s" % (
                         r.get("reason"), r.get("side"), r.get("volume")))

    print()
    for name in FORBIDDEN:
        rows = bus.events(name)
        if rows:
            print("FORBIDDEN EVENT REACHED THE PLATFORM PATH: %s x%d" % (name, len(rows)))
            failures += 1
    if previews and targets:
        target = targets[-1]
        wanted = round(float(target.get("delta_buy") or 0.0), 2)
        got = {round(float(p.get("volume") or 0.0), 2) for p in previews}
        if wanted > 0 and wanted not in got:
            print("PREVIEW VOLUME MISMATCH: target delta_buy=%s previews=%s" % (wanted, got))
            failures += 1
        else:
            print("E-proof: the 453-born target (delta_buy=%s) reached the gate as PREVIEW volumes=%s"
                  % (wanted, sorted(got)))
    if margins and not margins[-1].get("approved"):
        print("NOTE: margin rejected — chain still proven up to the verdict.")
    print()
    print("stages_missing_or_forbidden=%d" % failures)
    if failures == 0:
        print("OK: decision -> gate PREVIEW, and not one event headed to MT5.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run()))
