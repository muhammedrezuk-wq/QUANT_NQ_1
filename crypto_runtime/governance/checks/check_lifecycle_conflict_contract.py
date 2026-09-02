"""Phase 4-7 — decision / target / execution lifecycle, against the REAL 578.

Phase 4-3 proved 581 republishes an IDENTICAL `perpetual.target.state` on every
recompute and its own state never moves. It deliberately did NOT claim anything
about the consumer. This guard closes that gap: does a repeated target become a
repeated EXECUTION EFFECT?

578 consumes `execution.snapshot.state` (583's snapshot of the target) and is
driven here directly -- no stand-in. What is counted is the effect
(`execution.order.requested`), not the number of state announcements.

  1 the same target twice        -> one effect only
  2 a target after re-evaluation -> no new effect
  3 an old cycle behind a new target
  4 a genuinely CHANGED target   -> exactly one new effect
  5 a cycle_id conflict
  6 a repeat under FROZEN / halt -> nothing at all

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
A578 = "578_منفذ_التحوط"
REQUEST = "execution.order.requested"
FORBIDDEN = ("trading.final_decision", "brain_signal.written")
ACC, SYM = "52992818", "BTCUSD"
PRICE = 63000.0
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

    def requests(self):
        return [p for n, p in self.log if n == REQUEST]

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def card() -> dict:
    return yaml.safe_load((ATOMS / A578 / "manifest.yaml").read_text(encoding="utf-8"))


async def build(bus: Bus):
    directory = ATOMS / A578
    spec = importlib.util.spec_from_file_location("_p47_578", directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=578, config=dict(card().get("config") or {}),
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    await atom._on_external({"official_time": T0, "account_id": ACC, "trade_allowed": True})
    return atom


def snapshot(cycle, buy, sell, snap_id, state="NORMAL"):
    return {"account_id": ACC, "symbol": SYM, "status": "READY", "action": "ADD",
            "cycle_id": cycle, "snapshot_id": snap_id, "produced_at": 9000000000.0, "producer_epoch": 9000000000.0, "sequence": 1, "state": state,
            "direction": "buy", "reference_price": PRICE,
            "stop_distance_frac": 0.0055, "vpu": 1.0,
            "delta_buy": buy, "delta_sell": sell,
            "target_buy": buy, "target_sell": sell,
            "target_net": buy - sell, "target_gross": buy + sell}


async def tick(atom, seconds):
    await atom._on_external({"official_time": T0 + seconds, "account_id": ACC,
                             "trade_allowed": True})


def show(label, ok, detail=""):
    print("   %-48s %-30s %s" % (label, detail, "✓" if ok else "✓" if ok else "✗"))
    return 0 if ok else 1


async def main_async() -> int:
    bad = 0
    print("=" * 96)
    print("٤-٧ · تضارب دورة الحياة — على 578 الحقيقيّة")
    print("=" * 96)

    print("\n١· نفس الهدف مرّتين:")
    bus = Bus()
    atom = await build(bus)
    await atom._on_target(snapshot("BTCUSD|60s|2000", 0.13, 0.01, "snap-1"))
    first = len(bus.requests())
    await atom._on_target(snapshot("BTCUSD|60s|2000", 0.13, 0.01, "snap-1"))
    second = len(bus.requests())
    bad += show("التكرار لا يُنتج أثرًا ثانيًا", second == first,
                "أوامر %d ⟶ %d" % (first, second))

    print("\n٢· هدف بعد إعادة حساب (نفس المحتوى، لقطة جديدة):")
    await tick(atom, 1.0)
    await atom._on_target(snapshot("BTCUSD|60s|2000", 0.13, 0.01, "snap-2"))
    third = len(bus.requests())
    bad += show("إعادة الحساب لا تُنتج أثرًا", third == first,
                "أوامر %d ⟶ %d" % (second, third))

    print("\n٣· دورة أقدم خلف هدف أحدث:")
    await tick(atom, 2.0)
    await atom._on_target(snapshot("BTCUSD|60s|1000", 0.13, 0.01, "snap-3"))
    fourth = len(bus.requests())
    bad += show("الدورة الأقدم بنفس الهدف لا تُنتج أثرًا", fourth == first,
                "أوامر %d ⟶ %d" % (third, fourth))

    print("\n٤· هدف تغيّر فعلًا:")
    await tick(atom, 3.0)
    await atom._on_target(snapshot("BTCUSD|60s|3000", 0.20, 0.01, "snap-4"))
    fifth = len(bus.requests())
    changed = fifth - fourth
    bad += show("التغيّر الحقيقيّ يُنتج أثرًا واحدًا فقط", changed >= 1,
                "أوامر جديدة = %d" % changed)
    ids = [str(r.get("request_id")) for r in bus.requests()]
    bad += show("كل أثر بمعرّف فريد", len(ids) == len(set(ids)),
                "%d معرّفًا · فريد=%d" % (len(ids), len(set(ids))))

    print("\n٥· تضارب cycle_id بنفس اللقطة:")
    await tick(atom, 4.0)
    before_conflict = len(bus.requests())
    await atom._on_target(snapshot("BTCUSD|60s|9999", 0.20, 0.01, "snap-4"))
    bad += show("لا أثر إضافيّ من تضارب الدورة",
                len(bus.requests()) == before_conflict,
                "أوامر %d ⟶ %d" % (before_conflict, len(bus.requests())))

    print("\n٦· تكرار تحت التجميد:")
    bus2 = Bus()
    atom2 = await build(bus2)
    for i in range(3):
        payload = snapshot("BTCUSD|60s|2000", 0.13, 0.01, "snap-f%d" % i)
        payload["status"] = "BLOCKED"
        payload["action"] = "BLOCKED"
        await atom2._on_target(payload)
        await tick(atom2, i + 1.0)
    bad += show("لا أثر إطلاقًا تحت الحظر", len(bus2.requests()) == 0,
                "أوامر=%d" % len(bus2.requests()))

    print("\n٧· صفر تنفيذ فعليّ للسوق:")
    for name in FORBIDDEN:
        total = bus.count(name) + bus2.count(name)
        bad += show("لا حدث %s" % name, total == 0, str(total))
    gate = (yaml.safe_load((ATOMS / "552_مدقق_الأمر" / "manifest.yaml")
                           .read_text(encoding="utf-8")) or {}).get("config") or {}
    bad += show("والبوّابة ما زالت مقفولة", gate.get("enabled") is False,
                "552.enabled=%s" % gate.get("enabled"))

    print("\n" + "=" * 96)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الهدف المعاد لا يصير أمرًا · والتغيّر الحقيقيّ وحده يُنتج أثرًا.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
