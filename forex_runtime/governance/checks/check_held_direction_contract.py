"""Contract guard for problem 5-4 (option a) — the held-direction book must
survive a restart, and must never be forced onto a book that disagrees.

Owner's ruling 2026-08-14, verbatim:

    "581._held_dir is real sticky state and is NOT derived from the positions.
     Losing it explicitly breaks the REVERSAL_VIA_NEUTRAL contract after a
     restart."
    "held_dir is not restored merely because a snapshot exists. It is restored
     only if it agrees with the CURRENT live book, or the live state is
     neutral. On conflict the old held_dir is dropped and never forced onto the
     current decision."
    "Do not let a failed restore open the path. If the held_dir snapshot is
     corrupt or unverifiable, the state is conservative/closed per the existing
     protection contract -- no direction assumed from corrupt memory."
    "Do not touch 578 now."

The violation this guard demonstrates: with held = BUY and a live long book, a
SELL decision must return REVERSAL_VIA_NEUTRAL. After a restart held is None,
so the very same decision is ADOPTED immediately -- the position flips without
ever passing through neutral.

  A) STRUCTURAL -- 581 carries snapshot/restore for held_dir, the agreement
     rule and the fail-closed marker; 578 is untouched.
  B) الخرق      -- a continuous engine refuses the reversal; an engine that
     lost the book accepts it. Measured side by side.
  C) الاستعادة  -- field by field, and the refusal comes back with it.
  D) التعارض    -- a remembered BUY over a live SELL book is DROPPED.
  E) fail-closed -- a corrupt snapshot yields NO direction while the book is
     not flat.

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
A581 = "581_محرك_فرق_المركز"
A578 = "578_منفذ_التحوط"
ACC, SYM = "A", "GOLD"
PRICE, BUDGET, FRAC, VPU = 100.0, 50.0, 0.05, 1.0


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.log.append((name, payload))

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None


def load():
    directory = ATOMS / A581
    spec = importlib.util.spec_from_file_location("_cheld_581", directory / "atom.py")
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


async def engine(module, legs_buy=0.0, legs_sell=0.0):
    """A real 581 with a real book, ready to take decisions."""
    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=581, config=dict(manifest(A581)["config"]),
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    await atom._on_specs({"symbols": [{"symbol": SYM, "tick_value": VPU, "tick_size": 1.0}]})
    await atom._on_candle({"symbol": SYM, "close": PRICE})
    await atom._on_dial({"profiles": [{"account_id": ACC, "symbol": SYM,
                                       "stop_distance_frac": FRAC}]})
    await atom._on_portfolio({"portfolios": [{"account_id": ACC, "symbol": SYM,
                                              "state": "NORMAL", "protection_intent": "NONE",
                                              "v_net": legs_buy - legs_sell,
                                              "account_mode": "HEDGING"}]})
    rows = []
    if legs_buy > 0:
        rows.append({"account_id": ACC, "symbol": SYM, "side": "BUY", "ticket": 1,
                     "volume": legs_buy, "entry_price": PRICE})
    if legs_sell > 0:
        rows.append({"account_id": ACC, "symbol": SYM, "side": "SELL", "ticket": 2,
                     "volume": legs_sell, "entry_price": PRICE})
    await atom._on_positions({"source": "guard", "account_id": ACC, "positions": rows})
    await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                        "risk_budget": BUDGET,
                                        "v_net": legs_buy - legs_sell}]})
    return atom, bus


async def decide(atom, direction, strength, cycle):
    await atom._on_verdict({"symbol": SYM, "cycle_id": cycle, "metadata": {"approved": True}})
    await atom._on_decision({"account_id": ACC, "symbol": SYM, "cycle_id": cycle,
                             "direction": direction, "strength": strength})


def structural(module) -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة")
    print("=" * 78)
    bad = 0
    src = (ATOMS / A581 / "atom.py").read_text(encoding="utf-8")
    for label, ok in (
            ("581 يحفظ held_dir", "async def snapshot" in src and "held_dir" in src),
            ("581 يستعيد", "async def restore" in src),
            ("شرط الموافقة مع الكتاب الحيّ", "_settle_pending" in src),
            ("علامة fail-closed", "FAIL_CLOSED" in src)):
        bad += 0 if ok else 1
        print("  %-40s %s" % (label, "✓" if ok else "✗"))
    # 578 later moved to 2.7.0 for problem 58 (measurement only) and 2.8.0 for
    # problem 63 (request id identity).  Its behaviour stays frozen by
    # check_delta_visibility_contract and check_request_id_identity_contract.
    got = str(manifest(A578).get("version"))
    # Rebased 2026-08-15: item 4-10 moved 578 to 3.0.0 by the owner's order
    # (a replayed pre-crash snapshot may no longer become an order). This
    # barrier still means "5-4 did not touch 578" -- only its reference moves.
    ok = got == "3.0.0"
    bad += 0 if ok else 1
    print("  %-40s %-8s %s" % ("578 لم يتغيّر سلوكها", got, "✓" if ok else "✗ تغيّرت!"))
    return bad


async def main_async() -> int:
    module = load()
    bad = structural(module)

    print("\n" + "-" * 78)
    print("ب) الخرق — محرّك متّصل يرفض الانقلاب، ومحرّك فقد كتابه يقبله")
    print("-" * 78)
    live, _ = await engine(module, legs_buy=0.288)
    await decide(live, "buy", 0.95, "c1")          # يتبنّى الشراء
    held_before = dict(live._held_dir)
    await decide(live, "sell", 0.70, "c2")         # يطلب البيع والكتاب شراء
    cont = live._last.get(list(live._last)[0]) if live._last else None
    cont_net = (cont or {}).get("target_net")
    cont_reason = (cont or {}).get("reason")

    fresh, _ = await engine(module, legs_buy=0.288)   # نفس الكتاب، ذاكرة فارغة
    await decide(fresh, "sell", 0.70, "c2")
    lost = fresh._last.get(list(fresh._last)[0]) if fresh._last else None
    lost_net = (lost or {}).get("target_net")
    lost_reason = (lost or {}).get("reason")

    print("  متّصل  : held=%s → target_net=%-12s reason=%s" % (
        held_before, cont_net, cont_reason))
    print("  فقد الكتاب: held=%s → target_net=%-12s reason=%s" % (
        dict(fresh._held_dir), lost_net, lost_reason))
    refuses = cont_reason == "REVERSAL_VIA_NEUTRAL" and abs(float(cont_net or 0.0)) < 1e-12
    flips = lost_reason != "REVERSAL_VIA_NEUTRAL" and float(lost_net or 0.0) < 0.0
    print("  %-40s %s" % ("المتّصل يرفض الانقلاب", "✓" if refuses else "✗"))
    print("  %-40s %s" % ("فاقد الكتاب ينقلب بلا حياد", "🔴 نعم — هذا الخرق" if flips else "لا"))

    src = (ATOMS / A581 / "atom.py").read_text(encoding="utf-8")
    if "async def snapshot" not in src or "held_dir" not in src:
        bad += 1
        print("\n  ⇒ لا لقطة للكتاب ⇒ لا استعادة ⇒ الخرق قائم بعد كل إقلاع")
        return finish(bad)

    print("\n" + "-" * 78)
    print("ج+د+هـ) الاستعادة · التعارض · fail-closed")
    print("-" * 78)
    snap = await live.snapshot()
    print("  اللقطة: %s" % snap)

    # ج) الكتاب الحيّ يوافق ⇒ يُستعاد ويعود الرفض
    a, _ = await engine(module, legs_buy=0.288)
    await a.restore(snap)
    await decide(a, "sell", 0.70, "c3")
    r = a._last.get(list(a._last)[0]) if a._last else None
    ok = (r or {}).get("reason") == "REVERSAL_VIA_NEUTRAL"
    bad += 0 if ok else 1
    print("  %-40s %s  (held=%s)" % ("كتاب موافق ⇒ استُعيد والرفض عاد",
                                      "✓" if ok else "✗", dict(a._held_dir)))

    # د) الكتاب الحيّ يعارض (بيع) واللقطة تقول شراء ⇒ يُهمَل
    b, _ = await engine(module, legs_sell=0.288)
    await b.restore(snap)
    await decide(b, "sell", 0.70, "c4")
    forced = b._held_dir.get(ACC + module.SEP + SYM) == module.BUY
    bad += 0 if not forced else 1
    print("  %-40s %s  (held=%s)" % ("كتاب معارض ⇒ أُهمِل ولم يُفرَض",
                                      "✓" if not forced else "✗ فُرِض!", dict(b._held_dir)))

    # هـ) لقطة تالفة والكتاب غير محايد ⇒ لا اتّجاه
    c, _ = await engine(module, legs_buy=0.288)
    failed = False
    try:
        await c.restore({"held_dir": {"x": "SIDEWAYS"}})
    except Exception:
        failed = True
    await decide(c, "buy", 0.95, "c5")
    r = c._last.get(list(c._last)[0]) if c._last else None
    net = float((r or {}).get("target_net") or 0.0)
    ok = failed and abs(net) < 1e-12
    bad += 0 if ok else 1
    print("  %-40s %s  (رفعت الخطأ=%s · target_net=%s · reason=%s)" % (
        "لقطة تالفة ⇒ لا اتّجاه", "✓" if ok else "✗", failed, net, (r or {}).get("reason")))
    return finish(bad)


def finish(bad: int) -> int:
    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الكتاب ينجو، ولا يُفرَض على كتاب معارض، والفساد لا يفتح اتّجاهًا.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
