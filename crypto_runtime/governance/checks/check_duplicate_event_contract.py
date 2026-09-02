"""Phase 4-3 — a REAL literal duplicate, against the REAL 581.

A literal duplicate is the SAME event: same identity and same content, arriving
twice. It must be distinguished from three things that look like it:

  * a legitimate re-evaluation of the same cycle with DIFFERENT content (4-1);
  * an `approved` that follows a re-evaluation -- a consequence, not a decision;
  * a stale event from an older cycle (4-2).

Saying "duplicate" is not a result. Every case below drives the real atom and
compares the FULL final state: direction, net, gross, both legs, action, status,
verdict AND the deltas -- before and after the repeat.

Exit 1 on any divergence. Nothing is fixed here.
"""
from __future__ import annotations

import asyncio
import importlib.util
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
A581 = "581_محرك_فرق_المركز"
OUT = "perpetual.target.state"
FORBIDDEN = ("execution.order.requested", "trading.final_decision", "brain_signal.written")
ACC, SYM = "A", "BTCUSD"
PRICE, VPU, STOP_FRAC, BUDGET = 63000.0, 1.0, 0.0055, 100.0
NOW, OLD = "BTCUSD|60s|2000", "BTCUSD|60s|1000"
STATE = ("direction", "target_net", "target_gross", "target_buy", "target_sell",
         "action", "status", "filter_verdict", "delta_net", "delta_buy", "delta_sell")


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.log.append((name, dict(payload) if isinstance(payload, dict) else payload))

    def last(self):
        rows = [p for n, p in self.log if n == OUT]
        return rows[-1] if rows else None

    def emitted(self):
        return sum(1 for n, _ in self.log if n == OUT)

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def load():
    directory = ATOMS / A581
    spec = importlib.util.spec_from_file_location("_p43_581", directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def card() -> dict:
    return yaml.safe_load((ATOMS / A581 / "manifest.yaml").read_text(encoding="utf-8"))


def snap(state):
    return None if state is None else {f: state.get(f) for f in STATE}


async def fresh(module, portfolio="NORMAL"):
    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=581, config=dict(card().get("config") or {}),
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    await atom._on_specs({"symbols": [{"symbol": SYM, "tick_value": VPU, "tick_size": 1.0}]})
    await atom._on_candle({"symbol": SYM, "close": PRICE})
    await atom._on_dial({"profiles": [{"account_id": ACC, "symbol": SYM,
                                       "stop_distance_frac": STOP_FRAC}]})
    await atom._on_portfolio({"portfolios": [{"account_id": ACC, "symbol": SYM,
                                              "state": portfolio, "account_mode": "HEDGING"}]})
    await atom._on_positions({"source": "p43", "account_id": ACC, "positions": []})
    await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                        "risk_budget": BUDGET, "v_net": 0.0}]})
    bus.log.clear()
    return atom, bus


# The SAME objects are reused on purpose: a literal duplicate is byte-identical,
# not a look-alike rebuilt with fresh fields.
RESOLVED = {"symbol": SYM, "account_id": ACC, "cycle_id": NOW,
            "direction": "buy", "strength": 0.75}
APPROVED = {"symbol": SYM, "cycle_id": NOW, "metadata": {"approved": True}}
RESOLVED_OLD = {"symbol": SYM, "account_id": ACC, "cycle_id": OLD,
                "direction": "sell", "strength": 0.95}
APPROVED_OLD = {"symbol": SYM, "cycle_id": OLD, "metadata": {"approved": True}}


def show(label, ok, detail=""):
    print("   %-46s %-32s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def main_async() -> int:
    module = load()
    bad = 0
    print("=" * 96)
    print("٤-٣ · التكرار الحرفيّ الحقيقيّ — على 581 الحقيقيّة")
    print("=" * 96)

    print("\nأ) تكرار قبل المعالجة (نفس الحدث مرّتين ثمّ يُعالَج):")
    atom, bus = await fresh(module)
    await atom._on_decision(dict(RESOLVED))
    await atom._on_decision(dict(RESOLVED))            # literal duplicate, unprocessed yet
    await atom._on_verdict(dict(APPROVED))
    once = snap(bus.last())
    atom2, bus2 = await fresh(module)
    await atom2._on_decision(dict(RESOLVED))
    await atom2._on_verdict(dict(APPROVED))
    clean = snap(bus2.last())
    bad += show("النتيجة تطابق مسارًا بلا تكرار", once == clean,
                "صافي %s" % (once or {}).get("target_net"))

    print("\nب) تكرار بعد المعالجة:")
    atom, bus = await fresh(module)
    await atom._on_decision(dict(RESOLVED))
    await atom._on_verdict(dict(APPROVED))
    before = snap(bus.last())
    emitted_before = bus.emitted()
    await atom._on_decision(dict(RESOLVED))
    await atom._on_verdict(dict(APPROVED))
    after = snap(bus.last())
    bad += show("الحالة لم تتغيّر حرفًا", before == after,
                "%s ⟶ %s" % (before.get("target_net"), after.get("target_net")))
    bad += show("والأثر لم يُعَد تنفيذه (نفس الفروق)",
                before.get("delta_buy") == after.get("delta_buy")
                and before.get("delta_sell") == after.get("delta_sell"),
                "buy=%s · sell=%s" % (after.get("delta_buy"), after.get("delta_sell")))
    print("      ⓘ نشرات الهدف: %d ⟶ %d (إعلان حالة، لا أمر)"
          % (emitted_before, bus.emitted()))

    print("\nج) تكرار بعد إعادة حسم شرعيّة:")
    atom, bus = await fresh(module)
    await atom._on_decision({**RESOLVED, "direction": "wait", "strength": 0.0})
    await atom._on_verdict(dict(APPROVED))
    await atom._on_decision(dict(RESOLVED))            # the completed re-evaluation
    await atom._on_verdict(dict(APPROVED))
    settled = snap(bus.last())
    await atom._on_decision(dict(RESOLVED))            # literal duplicate on top
    await atom._on_verdict(dict(APPROVED))
    repeated = snap(bus.last())
    bad += show("التكرار فوق إعادة الحسم لا يغيّر شيئًا", settled == repeated,
                "%s · %s" % (repeated.get("direction"), repeated.get("target_net")))

    print("\nد) تكرار `resolved` وحده · وتكرار `approved` وحده:")
    atom, bus = await fresh(module)
    await atom._on_decision(dict(RESOLVED))
    await atom._on_verdict(dict(APPROVED))
    base = snap(bus.last())
    for _ in range(3):
        await atom._on_decision(dict(RESOLVED))
    only_resolved = snap(bus.last())
    bad += show("٣ تكرارات لـ resolved لا تُنشئ قرارًا ثانيًا", base == only_resolved,
                str(only_resolved.get("direction")))
    for _ in range(3):
        await atom._on_verdict(dict(APPROVED))
    only_approved = snap(bus.last())
    bad += show("٣ تكرارات لـ approved لا تفتح مسارًا جديدًا", base == only_approved,
                str(only_approved.get("filter_verdict")))

    print("\nهـ) تكرار من دورة قديمة يبقى stale:")
    atom, bus = await fresh(module)
    await atom._on_decision(dict(RESOLVED))
    await atom._on_verdict(dict(APPROVED))
    settled = snap(bus.last())
    for _ in range(3):
        await atom._on_verdict(dict(APPROVED_OLD))
        await atom._on_decision(dict(RESOLVED_OLD))
    stale = snap(bus.last())
    await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                        "risk_budget": BUDGET, "v_net": 0.0}]})
    later = snap(bus.last())
    bad += show("التكرار القديم لا يقلب الحالة", settled == stale,
                "%s ⟶ %s" % (settled.get("direction"), stale.get("direction")))
    bad += show("ولا يظهر أثره في إعادة حساب لاحقة", later == settled,
                "%s · %s" % (later.get("direction"), later.get("filter_verdict")))

    print("\nو) تكرار تحت التجميد:")
    atom, bus = await fresh(module, portfolio="FROZEN")
    for _ in range(3):
        await atom._on_decision(dict(RESOLVED))
        await atom._on_verdict(dict(APPROVED))
    frozen = snap(bus.last())
    bad += show("يبقى محظورًا مهما تكرّر",
                frozen.get("action") == "BLOCKED" and frozen.get("target_net") in (None, 0.0),
                "%s · صافي=%s" % (frozen.get("action"), frozen.get("target_net")))

    print("\nز) صفر تنفيذ:")
    for name in FORBIDDEN:
        bad += show("لا حدث %s" % name, bus.count(name) == 0, str(bus.count(name)))

    print("\n" + "=" * 96)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: التكرار الحرفيّ لا يُنتج قرارًا ولا أثرًا ولا يفكّ حظرًا.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
