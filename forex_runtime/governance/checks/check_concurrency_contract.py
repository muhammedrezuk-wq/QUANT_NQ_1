"""Phase 4-9 — concurrent pressure, against the REAL 581 and 578.

Everything up to here fed the atoms one event at a time. That is not how a bus
behaves under load. Here the events are genuinely interleaved with
`asyncio.gather`, across two symbols and two accounts, while an older cycle
pushes in and a halt lands mid-stream.

  1 concurrent cycles do not mix identity
  2 cycle_id / request_id never collide
  3 the SAME target arriving concurrently adds no extra execution effect
  4 changing one cycle's target does not move another's
  + resolved/approved in parallel · an old cycle mid-stream · duplicates from
    two "threads" · HALT during the pressure · recompute while a tick lands ·
    different targets for two accounts in the same instant.

The criterion is not that the guard prints green: it is that the FINAL state and
the execution effect are correct under every interleaving measured.

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
FOLDERS = {p.name.split("_")[0]: p.name for p in ATOMS.iterdir() if p.is_dir()}
TARGET = "perpetual.target.state"
REQUEST = "execution.order.requested"
FORBIDDEN = ("trading.final_decision", "brain_signal.written")
A1, A2 = "ACC-1", "ACC-2"
S1, S2 = "BTCUSD", "XAUUSD"
PRICE, T0 = 63000.0, 1_000_000.0


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

    def rows(self, name):
        return [p for n, p in self.log if n == name]

    def last_for(self, name, account, symbol):
        rows = [p for p in self.rows(name)
                if str(p.get("account_id")) == account and str(p.get("symbol")) == symbol]
        return rows[-1] if rows else None

    def count(self, name):
        return len(self.rows(name))


def card(atom_id: str) -> dict:
    return yaml.safe_load(
        (ATOMS / FOLDERS[atom_id] / "manifest.yaml").read_text(encoding="utf-8"))


async def build(atom_id: str, bus: Bus):
    directory = ATOMS / FOLDERS[atom_id]
    spec = importlib.util.spec_from_file_location("_p49_%s_%d" % (atom_id, id(bus)),
                                                  directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=int(atom_id), config=dict(card(atom_id).get("config") or {}),
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    return atom


async def prime(engine, pairs):
    await engine._on_specs({"symbols": [{"symbol": s, "tick_value": 1.0, "tick_size": 1.0}
                                        for _, s in pairs]})
    for account, symbol in pairs:
        await engine._on_candle({"symbol": symbol, "close": PRICE})
        await engine._on_dial({"profiles": [{"account_id": account, "symbol": symbol,
                                             "stop_distance_frac": 0.0055}]})
        await engine._on_portfolio({"portfolios": [{"account_id": account, "symbol": symbol,
                                                    "state": "NORMAL",
                                                    "account_mode": "HEDGING"}]})
        await engine._on_positions({"source": "p49", "account_id": account, "positions": []})
        await engine._on_ledger({"ledgers": [{"account_id": account, "symbol": symbol,
                                              "risk_budget": 100.0, "v_net": 0.0}]})


def decision(account, symbol, cycle, direction, strength):
    return {"symbol": symbol, "account_id": account, "cycle_id": cycle,
            "direction": direction, "strength": strength}


def verdict(symbol, cycle, approved=True):
    return {"symbol": symbol, "cycle_id": cycle, "metadata": {"approved": approved}}


def snapshot(account, symbol, cycle, snap_id, buy, sell):
    return {"account_id": account, "symbol": symbol, "status": "READY", "action": "ADD",
            "cycle_id": cycle, "snapshot_id": snap_id, "produced_at": 9000000000.0, "producer_epoch": 9000000000.0, "sequence": 1, "direction": "buy",
            "reference_price": PRICE, "stop_distance_frac": 0.0055, "vpu": 1.0,
            "delta_buy": buy, "delta_sell": sell, "target_buy": buy, "target_sell": sell,
            "target_net": buy - sell, "target_gross": buy + sell}


def show(label, ok, detail=""):
    print("   %-48s %-30s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def main_async() -> int:
    bad = 0
    print("=" * 96)
    print("٤-٩ · الضغط المتزامن — على 581 و578 الحقيقيّتين")
    print("=" * 96)

    print("\n١+٤· دورتان لحسابين ورمزين في اللحظة نفسها:")
    bus = Bus()
    engine = await build("581", bus)
    await prime(engine, [(A1, S1), (A2, S2)])
    await asyncio.gather(
        engine._on_decision(decision(A1, S1, "%s|60s|2000" % S1, "buy", 0.75)),
        engine._on_decision(decision(A2, S2, "%s|60s|2000" % S2, "sell", 0.75)),
        engine._on_verdict(verdict(S1, "%s|60s|2000" % S1)),
        engine._on_verdict(verdict(S2, "%s|60s|2000" % S2)),
    )
    one = bus.last_for(TARGET, A1, S1)
    two = bus.last_for(TARGET, A2, S2)
    bad += show("الهويّات لم تختلط",
                bool(one) and bool(two) and one.get("direction") == "buy"
                and two.get("direction") == "sell",
                "%s=%s · %s=%s" % (S1, (one or {}).get("direction"),
                                    S2, (two or {}).get("direction")))
    net_before = (one or {}).get("target_net")
    await asyncio.gather(
        engine._on_decision(decision(A1, S1, "%s|60s|3000" % S1, "buy", 0.45)),
        engine._on_verdict(verdict(S1, "%s|60s|3000" % S1)),
    )
    two_after = bus.last_for(TARGET, A2, S2)
    bad += show("٤· تغيّر دورة لا يمسّ الأخرى",
                two_after.get("direction") == "sell"
                and two_after.get("target_net") == two.get("target_net"),
                "%s ثابت=%s" % (S2, two_after.get("target_net")))
    changed = bus.last_for(TARGET, A1, S1)
    bad += show("والتي تغيّرت فعلًا تحرّكت وحدها",
                changed.get("target_net") != net_before,
                "%s ⟶ %s" % (net_before, changed.get("target_net")))

    print("\nدورة قديمة تقتحم وسط الضغط:")
    stable = bus.last_for(TARGET, A1, S1)
    await asyncio.gather(
        engine._on_verdict(verdict(S1, "%s|60s|1000" % S1)),
        engine._on_decision(decision(A1, S1, "%s|60s|1000" % S1, "sell", 0.95)),
        engine._on_ledger({"ledgers": [{"account_id": A1, "symbol": S1,
                                        "risk_budget": 100.0, "v_net": 0.0}]}),
    )
    after_old = bus.last_for(TARGET, A1, S1)
    bad += show("القديمة لا تقلب تحت التزامن",
                after_old.get("direction") == stable.get("direction"),
                "%s ⟶ %s" % (stable.get("direction"), after_old.get("direction")))

    print("\nإعادة حساب أثناء وصول تكّة:")
    await asyncio.gather(
        engine._on_tick({"symbol": S1, "price": PRICE + 5.0}),
        engine._on_decision(decision(A1, S1, "%s|60s|4000" % S1, "buy", 0.75)),
        engine._on_verdict(verdict(S1, "%s|60s|4000" % S1)),
        engine._on_tick({"symbol": S1, "price": PRICE + 7.0}),
    )
    final = bus.last_for(TARGET, A1, S1)
    bad += show("الحالة النهائيّة متّسقة",
                final.get("direction") == "buy" and final.get("filter_verdict") == "FILTER_PASSED",
                "%s · %s" % (final.get("direction"), final.get("filter_verdict")))

    print("\n٢+٣· الأثر التنفيذيّ تحت التزامن (578):")
    bus2 = Bus()
    executor = await build("578", bus2)
    await executor._on_external({"official_time": T0, "account_id": A1, "trade_allowed": True})
    await executor._on_external({"official_time": T0, "account_id": A2, "trade_allowed": True})
    await asyncio.gather(
        executor._on_target(snapshot(A1, S1, "%s|60s|2000" % S1, "sn-1", 0.13, 0.01)),
        executor._on_target(snapshot(A2, S2, "%s|60s|2000" % S2, "sn-2", 0.20, 0.02)),
    )
    distinct = len(bus2.requests()) if hasattr(bus2, "requests") else bus2.count(REQUEST)
    first_effect = bus2.count(REQUEST)
    bad += show("حسابان ⟶ أثران منفصلان", first_effect >= 2, "أوامر=%d" % first_effect)
    del distinct

    await asyncio.gather(
        executor._on_target(snapshot(A1, S1, "%s|60s|2000" % S1, "sn-1", 0.13, 0.01)),
        executor._on_target(snapshot(A1, S1, "%s|60s|2000" % S1, "sn-1", 0.13, 0.01)),
        executor._on_target(snapshot(A1, S1, "%s|60s|2000" % S1, "sn-1", 0.13, 0.01)),
    )
    bad += show("٣· تكرار متزامن من ثلاثة خيوط بلا أثر زائد",
                bus2.count(REQUEST) == first_effect,
                "أوامر %d ⟶ %d" % (first_effect, bus2.count(REQUEST)))
    ids = [str(r.get("request_id")) for r in bus2.rows(REQUEST)]
    bad += show("٢· لا تصادم معرّفات", len(ids) == len(set(ids)),
                "%d · فريد=%d" % (len(ids), len(set(ids))))
    accounts = {str(r.get("account_id")) for r in bus2.rows(REQUEST)}
    bad += show("وكل أثر يحمل حسابه", accounts == {A1, A2}, " · ".join(sorted(accounts)))

    print("\nالإيقاف أثناء الضغط:")
    before_halt = bus2.count(REQUEST)
    await executor._on_external({"official_time": T0 + 60.0, "account_id": A1,
                                 "trade_allowed": False})
    await asyncio.gather(
        executor._on_target(snapshot(A1, S1, "%s|60s|5000" % S1, "sn-9", 0.55, 0.01)),
        executor._on_target(snapshot(A1, S1, "%s|60s|5000" % S1, "sn-9", 0.55, 0.01)),
    )
    bad += show("لا أثر بعد الإيقاف رغم التزامن",
                bus2.count(REQUEST) == before_halt,
                "أوامر %d ⟶ %d" % (before_halt, bus2.count(REQUEST)))

    print("\nصفر أمر للسوق:")
    for name in FORBIDDEN:
        total = bus.count(name) + bus2.count(name)
        bad += show("لا حدث %s" % name, total == 0, str(total))

    print("\n" + "=" * 96)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الهويّات لا تختلط · ولا معرّف يتصادم · ولا أثر زائد تحت التزامن.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
