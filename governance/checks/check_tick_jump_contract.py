"""Phase 4-5 — TICK_JUMP, against the REAL 521 and the REAL 581.

Item 77 released a latch: `jump_ok` used to return before writing the reference,
so one rejection pinned the comparison to a dead price and every later tick was
measured against it -- 90 of 90 rejected with a 200x margin. The fix writes the
reference on EVERY tick, then judges.

That fix has a consequence which must be MEASURED, not assumed: after a spike,
the comparison baseline is the spike itself. This guard records exactly what
happens to the next ticks, and -- the owner's sharp point -- whether the
PUBLISHED reference is ever contaminated by the anomalous price.

Rejecting the jump is not enough. The state itself must stay clean.

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
A521, A581 = "521_صحة_المرجع", "581_محرك_فرق_المركز"
FORBIDDEN = ("execution.order.requested", "trading.final_decision", "brain_signal.written")
ACC, SYM = "A", "BTCUSD"
BASE, SPIKE = 63000.0, 70000.0
T0 = 1_000_000.0


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

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def card(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


async def build(folder: str, atom_id: int, bus: Bus, overrides=None):
    directory = ATOMS / folder
    spec = importlib.util.spec_from_file_location("_p45_%d" % atom_id, directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    config = dict(card(folder).get("config") or {})
    config.update(overrides or {})
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=atom_id, config=config, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    return atom


def show(label, ok, detail=""):
    print("   %-44s %-36s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def main_async() -> int:
    bad = 0
    print("=" * 98)
    print("٤-٥ · القفزة السعريّة TICK_JUMP — على 521 و581 الحقيقيّتين")
    print("=" * 98)

    bus = Bus()
    atom = await build(A521, 521, bus, {"symbols": [SYM]})
    limit = float((card(A521).get("config") or {}).get("max_tick_jump") or 500.0)
    print("   العتبة المعلَنة: %.0f" % limit)

    async def tick(price, offset):
        atom._now = T0 + offset
        await atom._on_primary({"symbol": SYM, "bid": price - 0.5, "ask": price + 0.5,
                                "price": price, "timestamp": T0 + offset})
        state = bus.last("reference.health.state")
        primary = (state or {}).get("primary") or {}
        return primary.get("valid"), primary.get("reason"), (state or {}).get("selected_price")

    print("\nالتسلسل المقيس (سعر ⟶ صالح · سبب · السعر المنشور):")
    steps = []
    for label, price, offset in (("أساس", BASE, 0.0), ("قفزة", SPIKE, 1.0),
                                 ("عودة قريبة من الأساس", BASE + 1.0, 2.0),
                                 ("تكّة سليمة تالية", BASE + 2.0, 3.0),
                                 ("وتكّة ثالثة", BASE + 3.0, 4.0)):
        valid, reason, published = await tick(price, offset)
        steps.append((label, price, valid, reason, published))
        print("      %-24s %-9.0f ⟶ صالح=%-6s · %-22s · منشور=%s"
              % (label, price, valid, reason or "-", published))

    bad += show("٤· القفزة تُرفض", steps[1][2] is False and steps[1][3] == "TICK_JUMP",
                str(steps[1][3]))
    bad += show("٥· السعر الشاذّ لا يُنشَر مرجعًا",
                all(s[4] != SPIKE for s in steps), "لم يُنشر %.0f قطّ" % SPIKE)
    rebuilt = [s for s in steps[2:] if s[2] is True]
    bad += show("٧· المرجع يُعاد بناؤه من السليم", bool(rebuilt),
                "أوّل قبول: %s" % (rebuilt[0][0] if rebuilt else "لم يحدث"))

    print("\n٨· إعادة القفزة مرّة ثانية (حتى لا ينجح الحارس صدفةً):")
    again = []
    for label, price, offset in (("قفزة ثانية", SPIKE, 5.0),
                                 ("سليمة بعدها", BASE + 4.0, 6.0),
                                 ("سليمة أخرى", BASE + 5.0, 7.0)):
        valid, reason, published = await tick(price, offset)
        again.append((label, valid, reason, published))
        print("      %-24s %-9.0f ⟶ صالح=%-6s · %-22s · منشور=%s"
              % (label, price, valid, reason or "-", published))
    bad += show("القفزة الثانية تُرفض أيضًا",
                again[0][1] is False and again[0][2] == "TICK_JUMP", str(again[0][2]))
    bad += show("ولا تُنشَر مرجعًا", all(a[3] != SPIKE for a in again), "✓")

    print("\n٩· القرار 581 لم يُبنَ على السعر الشاذّ:")
    bus2 = Bus()
    engine = await build(A581, 581, bus2)
    await engine._on_specs({"symbols": [{"symbol": SYM, "tick_value": 1.0, "tick_size": 1.0}]})
    await engine._on_tick({"symbol": SYM, "price": BASE})
    await engine._on_dial({"profiles": [{"account_id": ACC, "symbol": SYM,
                                         "stop_distance_frac": 0.0055}]})
    await engine._on_portfolio({"portfolios": [{"account_id": ACC, "symbol": SYM,
                                                "state": "NORMAL", "account_mode": "HEDGING"}]})
    await engine._on_positions({"source": "p45", "account_id": ACC, "positions": []})
    await engine._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                          "risk_budget": 100.0, "v_net": 0.0}]})
    await engine._on_decision({"symbol": SYM, "account_id": ACC,
                               "cycle_id": "BTCUSD|60s|2000", "direction": "buy",
                               "strength": 0.75})
    await engine._on_verdict({"symbol": SYM, "cycle_id": "BTCUSD|60s|2000",
                              "metadata": {"approved": True}})
    clean = bus2.last("perpetual.target.state")
    bad += show("المرجع في الهدف هو السعر السليم",
                bool(clean) and clean.get("reference_price") == BASE,
                str((clean or {}).get("reference_price")))
    for name in FORBIDDEN:
        bad += show("١٠· لا حدث %s" % name, bus2.count(name) == 0, str(bus2.count(name)))

    print("\n" + "=" * 98)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: القفزة تُرفض ولا تُنشَر · والمرجع يُعاد بناؤه · والقرار على السليم.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
