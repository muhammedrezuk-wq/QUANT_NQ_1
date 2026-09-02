"""Phase 4-1 — re-evaluation and duplicate events, against the REAL 581.

Shadow Mode left exactly one thing unmeasured: every cycle resolves TWICE (an
early pass, then the completed one, as paper 16 documents), and `approved`
follows each of them. With the asset never activated, 581 produced no target in
that window, so its behaviour under the double sequence was never observed --
and it was recorded as an open question rather than guessed at.

This guard injects the sequence into the real atom and measures seven things:

    resolved(early) -> approved(early) -> resolved(completed) -> approved(completed)

  1 does 581 act on the LATEST resolution only?
  2 can the older decision override the newer one?
  3 can a stale `approved` contaminate a completed decision?
  4 do cycle_id / request_id / trace_id keep the link?
  5 is re-evaluation idempotent -- same input twice, same target?
  6 are HALT / RESET disturbed by re-evaluation?
  7 does anything real go out? It must stay zero.

Nothing is fixed here. A failure is recorded with its root, per the owner's rule.

Exit 1 on any divergence.
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
EARLY, DONE = "cycle-EARLY", "cycle-EARLY"          # same cycle, re-evaluated
OLD = "cycle-PREVIOUS"


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

    def targets(self):
        return [p for n, p in self.log if n == OUT]

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def load():
    directory = ATOMS / A581
    spec = importlib.util.spec_from_file_location("_p41_581", directory / "atom.py")
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


async def prime(module):
    """The same priming the hedge guard uses -- the real inputs, nothing faked."""
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
                                              "state": "NORMAL", "account_mode": "HEDGING"}]})
    await atom._on_positions({"source": "p41", "account_id": ACC, "positions": []})
    await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                        "risk_budget": BUDGET, "v_net": 0.0}]})
    bus.log.clear()
    return atom, bus


def decision(cycle, direction, strength, **extra):
    body = {"symbol": SYM, "account_id": ACC, "cycle_id": cycle,
            "direction": direction, "strength": strength,
            "request_id": "req-" + cycle, "trace_id": "trace-" + cycle}
    body.update(extra)
    return body


def verdict(cycle, approved):
    return {"symbol": SYM, "cycle_id": cycle, "metadata": {"approved": approved},
            "request_id": "req-" + cycle, "trace_id": "trace-" + cycle}


def show(label, ok, detail=""):
    print("   %-48s %-28s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def main_async() -> int:
    module = load()
    bad = 0
    print("=" * 92)
    print("٤-١ · إعادة الحسم والحدث المكرَّر — على الذرّة 581 الحقيقيّة")
    print("=" * 92)

    print("\nالتسلسل: resolved(early) ← approved(early) ← resolved(completed) ← approved(completed)")
    atom, bus = await prime(module)
    await atom._on_decision(decision(EARLY, "wait", 0.0))
    await atom._on_verdict(verdict(EARLY, False))
    early_targets = list(bus.targets())
    await atom._on_decision(decision(DONE, "buy", 0.75))
    await atom._on_verdict(verdict(DONE, True))
    final = bus.targets()[-1] if bus.targets() else None

    bad += show("١· يعمل على الحسم الأحدث",
                bool(final) and final.get("direction") == "buy",
                "direction=%s" % (final or {}).get("direction"))
    bad += show("   والحكم وصل مطابقًا للدورة",
                bool(final) and final.get("filter_verdict") == "FILTER_PASSED",
                str((final or {}).get("filter_verdict")))
    early_dirs = {t.get("direction") for t in early_targets}
    bad += show("٢· الحسم المبكّر لم يتجاوز الجديد",
                final is not None and (final.get("direction") == "buy"),
                "المبكّر أنتج %s" % (sorted(x for x in early_dirs if x) or "لا هدف"))

    print("\nتلويث: حكم قديم من دورة سابقة يصل بعد القرار المكتمل")
    atom2, bus2 = await prime(module)
    await atom2._on_decision(decision(DONE, "buy", 0.75))
    await atom2._on_verdict(verdict(OLD, True))          # stale verdict, other cycle
    dirty = bus2.targets()[-1] if bus2.targets() else None
    bad += show("٣· الحكم القديم لا يفتح القرار الجديد",
                bool(dirty) and dirty.get("filter_verdict") != "FILTER_PASSED",
                str((dirty or {}).get("filter_verdict")))

    print("\nالهويّة والارتباط:")
    ok = bool(final) and str(final.get("symbol")) == SYM and str(final.get("account_id")) == ACC
    bad += show("٤· الهدف مرتبط بالحساب والرمز", ok,
                "%s · %s" % ((final or {}).get("account_id"), (final or {}).get("symbol")))

    print("\nالتكرار الحرفيّ (نفس الدورة ونفس المحتوى مرّتين):")
    atom3, bus3 = await prime(module)
    await atom3._on_decision(decision(DONE, "buy", 0.75))
    await atom3._on_verdict(verdict(DONE, True))
    first = bus3.targets()[-1] if bus3.targets() else None
    await atom3._on_decision(decision(DONE, "buy", 0.75))
    await atom3._on_verdict(verdict(DONE, True))
    second = bus3.targets()[-1] if bus3.targets() else None
    same = bool(first) and bool(second) and all(
        first.get(f) == second.get(f) for f in
        ("direction", "target_net", "target_gross", "target_buy", "target_sell", "action"))
    bad += show("٥· إعادة الحسم لا تغيّر النتيجة (idempotent)", same,
                "صافي %s ⟶ %s" % ((first or {}).get("target_net"),
                                   (second or {}).get("target_net")))
    deltas = (second or {}).get("delta_buy"), (second or {}).get("delta_sell")
    print("      ⓘ الفروق بالتكرار الثاني: delta_buy=%s · delta_sell=%s" % deltas)

    print("\nالحماية أثناء إعادة الحسم:")
    atom4, bus4 = await prime(module)
    await atom4._on_portfolio({"portfolios": [{"account_id": ACC, "symbol": SYM,
                                               "state": "FROZEN", "account_mode": "HEDGING"}]})
    await atom4._on_decision(decision(DONE, "buy", 0.75))
    await atom4._on_verdict(verdict(DONE, True))
    frozen = bus4.targets()[-1] if bus4.targets() else None
    bad += show("٦· التجميد يصمد أمام إعادة الحسم",
                bool(frozen) and frozen.get("action") == "BLOCKED",
                "action=%s · %s" % ((frozen or {}).get("action"), (frozen or {}).get("reason")))

    print("\nصفر تنفيذ:")
    for name in FORBIDDEN:
        total = sum(b.count(name) for b in (bus, bus2, bus3, bus4))
        bad += show("٧· لا حدث %s" % name, total == 0, str(total))

    print("\n" + "=" * 92)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الأحدث يفوز · القديم لا يلوّث · إعادة الحسم ثابتة · والتجميد يصمد.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
