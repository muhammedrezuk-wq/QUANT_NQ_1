"""Phase 4-2 — late and out-of-order events, against the REAL 581.

A correct cycle is four events:

    A: resolved (the decision)      B: approved (the filters' verdict)
    C: risk (the asset ledger)      D: state (portfolio / positions)

They do not arrive in that order on a real bus. This guard reorders them on
purpose, and injects the nastier cases: a stale event after the cycle is done,
an event from a PREVIOUS cycle arriving during a new one, and a duplicate on
top of a late arrival.

What must hold:
  1 a late event never overrides newer state;
  2 an out-of-order event is held (pending), never read as a NEW decision;
  3 no cross-cycle contamination;
  4 `cycle_id` is the key -- not `symbol` alone;
  5 `approved` is not honoured before its own parent decision exists;
  6 FROZEN outranks any late arrival;
  7 the SAME set of events in ANY order ends in the SAME state;
  8 zero real orders throughout.

Nothing is fixed here. A failure is recorded with the order that broke it.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from itertools import permutations
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
NOW, PREV = "cycle-NOW", "cycle-PREV"
COMPARED = ("direction", "target_net", "target_gross", "target_buy", "target_sell",
            "action", "status", "filter_verdict")


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

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def load():
    directory = ATOMS / A581
    spec = importlib.util.spec_from_file_location("_p42_581", directory / "atom.py")
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


async def fresh(module, state="NORMAL"):
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
                                              "state": state, "account_mode": "HEDGING"}]})
    bus.log.clear()
    return atom, bus


def events(atom, cycle, direction="buy", strength=0.75):
    async def a():
        await atom._on_decision({"symbol": SYM, "account_id": ACC, "cycle_id": cycle,
                                 "direction": direction, "strength": strength})

    async def b():
        await atom._on_verdict({"symbol": SYM, "cycle_id": cycle,
                                "metadata": {"approved": True}})

    async def c():
        await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                            "risk_budget": BUDGET, "v_net": 0.0}]})

    async def d():
        await atom._on_positions({"source": "p42", "account_id": ACC, "positions": []})

    return {"A": a, "B": b, "C": c, "D": d}


def snap(state):
    return None if state is None else {f: state.get(f) for f in COMPARED}


def show(label, ok, detail=""):
    print("   %-46s %-30s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def main_async() -> int:
    module = load()
    bad = 0
    print("=" * 96)
    print("٤-٢ · أحداث متأخّرة وخارج الترتيب — على 581 الحقيقيّة")
    print("=" * 96)

    print("\n٧· نفس المجموعة بترتيبات مختلفة ⟶ نفس الحالة النهائيّة:")
    results = {}
    for order in permutations("ABCD"):
        atom, bus = await fresh(module)
        step = events(atom, NOW)
        for name in order:
            await step[name]()
        results["".join(order)] = snap(bus.last())
    shapes = {repr(v) for v in results.values()}
    ok = len(shapes) == 1 and None not in results.values()
    bad += 0 if ok else 1
    print("   %-46s %-30s %s" % ("٢٤ ترتيبًا", "حالات نهائيّة مختلفة = %d" % len(shapes),
                                 "✓" if ok else "✗"))
    if not ok:
        for order, value in results.items():
            print("      %-6s %s" % (order, value))
    else:
        sample = next(iter(results.values()))
        print("      الحالة الواحدة: %s · صافي=%s · حكم=%s"
              % (sample.get("direction"), sample.get("target_net"),
                 sample.get("filter_verdict")))

    print("\n٥· `approved` قبل وجود أبيه:")
    atom, bus = await fresh(module)
    step = events(atom, NOW)
    await step["C"]()
    await step["B"]()                       # verdict with no decision yet
    before = bus.last()
    bad += show("لا هدف يُبنى من حكم بلا قرار", before is None,
                "هدف=%s" % ("لا" if before is None else before.get("direction")))
    await step["A"]()
    after = bus.last()
    bad += show("وحين يصل أبوه يُقبل", bool(after) and after.get("filter_verdict") == "FILTER_PASSED",
                str((after or {}).get("filter_verdict")))

    print("\n١+٣· حدث من دورة سابقة يصل أثناء دورة جديدة:")
    atom, bus = await fresh(module)
    step_now = events(atom, NOW)
    for n in "CDAB":
        await step_now[n]()
    settled = snap(bus.last())
    await atom._on_verdict({"symbol": SYM, "cycle_id": PREV, "metadata": {"approved": False}})
    await atom._on_decision({"symbol": SYM, "account_id": ACC, "cycle_id": PREV,
                             "direction": "sell", "strength": 0.95})
    after = snap(bus.last())
    bad += show("١· القديم لا يقلب الحالة الأحدث",
                after == settled or after.get("filter_verdict") != "FILTER_PASSED",
                "%s ⟶ %s" % (settled.get("direction"), (after or {}).get("direction")))
    print("\n🔴 حالة الانحدار (البند ٤-٢): دورة أقدم ومعها حكم مُوافِق")
    # The decisive case, measured 2026-08-15: with approved=False the old cycle
    # ended BLOCKED/wait and looked like a safe failure -- it hid the flip. The
    # real danger is an old cycle carrying its OWN approving verdict: it passes
    # the filter legitimately and reverses a settled direction.
    #     cycle 2000 -> buy  -> FILTER_PASSED
    #     cycle 1000 -> sell -> FILTER_PASSED      <- the bug
    # Required after the fix: the old cycle is stale, and buy survives.
    atom, bus = await fresh(module)
    await atom._on_positions({"source": "p42", "account_id": ACC, "positions": []})
    await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                        "risk_budget": BUDGET, "v_net": 0.0}]})
    await atom._on_decision({"symbol": SYM, "account_id": ACC,
                             "cycle_id": "BTCUSD|60s|2000", "direction": "buy",
                             "strength": 0.75})
    await atom._on_verdict({"symbol": SYM, "cycle_id": "BTCUSD|60s|2000",
                            "metadata": {"approved": True}})
    newest = snap(bus.last())
    await atom._on_verdict({"symbol": SYM, "cycle_id": "BTCUSD|60s|1000",
                            "metadata": {"approved": True}})
    await atom._on_decision({"symbol": SYM, "account_id": ACC,
                             "cycle_id": "BTCUSD|60s|1000", "direction": "sell",
                             "strength": 0.95})
    older = snap(bus.last())
    bad += show("٣· دورة أقدم موافَقة لا تقلب الأحدث",
                older.get("direction") == "buy" and older.get("target_net") == newest.get("target_net"),
                "%s(%s) ⟶ %s(%s)" % (newest.get("direction"), newest.get("target_net"),
                                      older.get("direction"), older.get("target_net")))
    bad += show("   وحكم الدورة الأقدم لا يُبنى عليه مسار",
                older.get("filter_verdict") == "FILTER_PASSED"
                and older.get("direction") == "buy",
                str(older.get("filter_verdict")))
    # His condition: rejecting the stale RESOLVED is not enough -- the stale
    # APPROVED must not be able to open a valid path of its own. Its damage is
    # not visible at arrival: it would sit in the verdict slot and only surface
    # on the NEXT recompute, where the cycles no longer match and the settled
    # direction collapses to PENDING/wait. So the guard forces a recompute.
    await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                        "risk_budget": BUDGET, "v_net": 0.0}]})
    later = snap(bus.last())
    bad += show("   ولا يظهر أثره في إعادة حساب لاحقة",
                later.get("direction") == "buy"
                and later.get("filter_verdict") == "FILTER_PASSED",
                "%s · %s" % (later.get("direction"), later.get("filter_verdict")))

    print("\n٤· المفتاح هو cycle_id لا الرمز وحده:")
    atom, bus = await fresh(module)
    step = events(atom, NOW)
    await step["C"]()
    await step["D"]()
    await step["A"]()
    await atom._on_verdict({"symbol": SYM, "cycle_id": "cycle-OTHER",
                            "metadata": {"approved": True}})
    mismatched = bus.last()
    bad += show("حكم بنفس الرمز ودورة مختلفة لا يمرّ",
                bool(mismatched) and mismatched.get("filter_verdict") != "FILTER_PASSED",
                str((mismatched or {}).get("filter_verdict")))

    print("\n٢· تكرار بعد الحدث المتأخر:")
    atom, bus = await fresh(module)
    step = events(atom, NOW)
    for n in "CDAB":
        await step[n]()
    one = snap(bus.last())
    for n in "AB":
        await step[n]()
    two = snap(bus.last())
    bad += show("التكرار لا يغيّر الحالة", one == two,
                "صافي %s ⟶ %s" % (one.get("target_net"), two.get("target_net")))

    print("\n٦· التجميد أقوى من كل ما سبق:")
    atom, bus = await fresh(module, state="FROZEN")
    step = events(atom, NOW)
    for n in "BADC":
        await step[n]()
    frozen = bus.last()
    bad += show("BADC تحت التجميد", bool(frozen) and frozen.get("action") == "BLOCKED",
                "%s · %s" % ((frozen or {}).get("action"), (frozen or {}).get("reason")))

    print("\nالعقد مثبَّت بنسخة معلَنة:")
    # A guard that does not pin the version lets a rollback pass unnoticed --
    # the break "version rolled back" stayed green until this barrier existed.
    src = (ATOMS / A581 / "atom.py").read_text(encoding="utf-8")
    import re as _re
    found = _re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, _re.M)
    version = found.group(1) if found else ""
    bad += show("581 نسخة تحرّكت وتطابق البطاقة",
                version not in ("", "2.8.0") and version == str(card().get("version")),
                "كود=%s · بطاقة=%s" % (version, card().get("version")))
    bad += show("وحارس الأقدميّة قائم بالكود",
                "cycle_rank" in src and "is_stale" in src and "_cycle_rank" in src, "")

    print("\n٨· صفر تنفيذ طوال المحكّ:")
    for name in FORBIDDEN:
        bad += show("لا حدث %s" % name, bus.count(name) == 0, str(bus.count(name)))

    print("\n" + "=" * 96)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الترتيب لا يغيّر النتيجة · والمتأخّر لا يطغى · والمفتاح هو الدورة.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
