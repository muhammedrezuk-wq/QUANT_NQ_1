"""Phase 4-4 — transport loss and reconnect, against the REAL atoms.

A disconnect is not an event anyone publishes: it is the ABSENCE of events while
the clock keeps moving. So the danger is never a loud failure -- it is silence
being read as health, and old data being carried forward as if it were live.

Three real atoms hold the verdict and are driven here, not simulated:

    521  the reference -- must declare STALE when its data ages out, and must
         rebuild from the NEW tick after reconnect, not from the price it froze.
    619  the account -- must keep speaking and say how old it is (item 55).
    581  the decision -- must not invent a target during the silence, must not
         resurrect the pre-cut cycle, and must adopt the post-cut one.

And the point that matters most: losing the connection must never become an
order, nor an implicit approval.

Exit 1 on any divergence. Nothing is fixed here.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
import tempfile
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
FORBIDDEN = ("execution.order.requested", "trading.final_decision", "brain_signal.written")
ACC, SYM = "A", "BTCUSD"
PRICE, VPU, STOP_FRAC, BUDGET = 63000.0, 1.0, 0.0055, 100.0
T0 = 1_000_000.0
BEFORE_CYCLE, AFTER_CYCLE = "BTCUSD|60s|1000", "BTCUSD|60s|2000"


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.wired = {}

    def subscribe(self, name, handler):
        self.wired.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.log.append((name, dict(payload) if isinstance(payload, dict) else payload))

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def card(atom_id: str) -> dict:
    return yaml.safe_load(
        (ATOMS / FOLDERS[atom_id] / "manifest.yaml").read_text(encoding="utf-8"))


async def build(atom_id: str, bus: Bus, overrides: dict | None = None):
    directory = ATOMS / FOLDERS[atom_id]
    spec = importlib.util.spec_from_file_location("_p44_%s" % atom_id, directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    config = dict(card(atom_id).get("config") or {})
    config.update(overrides or {})
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=int(atom_id), config=config, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    return atom


def show(label, ok, detail=""):
    print("   %-46s %-32s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def reference() -> int:
    print("\nأ) المرجع 521 — الصمت لا يُقرأ صحّة")
    bad = 0
    bus = Bus()
    atom = await build("521", bus, {"symbols": [SYM]})
    max_age = float((card("521").get("config") or {}).get("max_data_age_s") or 5.0)

    async def tick(price, stamp):
        atom._now = stamp
        await atom._on_primary({"symbol": SYM, "bid": price - 0.5, "ask": price + 0.5,
                                "price": price, "timestamp": stamp})

    await tick(PRICE, T0)
    live = bus.last("reference.health.state")
    bad += show("١· قبل القطع: مرجع صالح",
                bool(live) and live.get("state") in ("HEALTHY", "FALLBACK"),
                "%s · %s" % ((live or {}).get("state"), (live or {}).get("selected_price")))

    # 2+3: the cut. No ticks arrive, but the clock keeps advancing.
    atom._now = T0 + max_age * 4
    await atom._publish(SYM)
    cut = bus.last("reference.health.state")
    bad += show("٣· أثناء القطع: لا يُبنى مرجع من القديم",
                bool(cut) and cut.get("state") not in ("HEALTHY", "FALLBACK"),
                "%s · عمر=%.0fs" % ((cut or {}).get("state"), (cut or {}).get("data_age_s") or 0))

    # 4+5+6: reconnect with a DIFFERENT price.
    await tick(PRICE + 500.0, T0 + max_age * 4 + 1.0)
    back = bus.last("reference.health.state")
    bad += show("٦· بعد العودة: الحالة من الجديد لا القديم",
                bool(back) and back.get("state") in ("HEALTHY", "FALLBACK")
                and back.get("selected_price") == PRICE + 500.0,
                "%s · %s" % ((back or {}).get("state"), (back or {}).get("selected_price")))
    return bad


async def account() -> int:
    print("\nب) الحساب 619 — يقول عمره بدل أن يصمت")
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "acct.db")

        def write(stamp):
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY,"
                         " account_id TEXT, balance REAL, equity REAL, margin REAL,"
                         " free_margin REAL, margin_level REAL, currency TEXT,"
                         " leverage INTEGER, open_count INTEGER, broker TEXT,"
                         " account_server TEXT, margin_mode TEXT, trade_allowed INTEGER,"
                         " connected INTEGER, updated_at REAL)")
            conn.execute("DELETE FROM account")
            conn.execute("INSERT INTO account VALUES (1,?,644.84,644.84,0.0,644.84,0.0,"
                         "'USD',5000,0,'B','S','HEDGING',1,1,?)", (ACC, stamp))
            conn.commit()
            conn.close()

        write(T0 - 10.0)
        bus = Bus()
        atom = await build("619", bus, {"db_path": db, "max_age_s": 300.0})
        await atom.stop()
        for handler in bus.wired.get("SYS_SECOND", []):
            await handler({"official_time": T0})
        await atom._read_once()
        fresh_state = bus.last("platform.account.state")
        bad += show("١· قبل القطع: حديث", bool(fresh_state) and fresh_state.get("stale") is False,
                    "عمر=%s" % (fresh_state or {}).get("age_s"))

        bus.log.clear()
        for handler in bus.wired.get("SYS_SECOND", []):
            await handler({"official_time": T0 + 4000.0})     # clock moves, row does not
        await atom._read_once()
        cut = bus.last("platform.account.state")
        bad += show("٣· أثناء القطع: يعلن تقادمه ولا يصمت",
                    bool(cut) and cut.get("stale") is True,
                    "عمر=%s · متقادم=%s" % ((cut or {}).get("age_s"), (cut or {}).get("stale")))

        write(T0 + 4100.0)
        bus.log.clear()
        for handler in bus.wired.get("SYS_SECOND", []):
            await handler({"official_time": T0 + 4101.0})
        await atom._read_once()
        back = bus.last("platform.account.state")
        bad += show("٦· بعد العودة: يعود حديثًا من الصفّ الجديد",
                    bool(back) and back.get("stale") is False,
                    "عمر=%s" % (back or {}).get("age_s"))
    return bad


async def decision() -> int:
    print("\nج) القرار 581 — الصمت ليس موافقة، والقديم لا يُبعث")
    bad = 0
    bus = Bus()
    atom = await build("581", bus)
    await atom._on_specs({"symbols": [{"symbol": SYM, "tick_value": VPU, "tick_size": 1.0}]})
    await atom._on_candle({"symbol": SYM, "close": PRICE})
    await atom._on_dial({"profiles": [{"account_id": ACC, "symbol": SYM,
                                       "stop_distance_frac": STOP_FRAC}]})
    await atom._on_portfolio({"portfolios": [{"account_id": ACC, "symbol": SYM,
                                              "state": "NORMAL", "account_mode": "HEDGING"}]})
    await atom._on_positions({"source": "p44", "account_id": ACC, "positions": []})
    await atom._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                        "risk_budget": BUDGET, "v_net": 0.0}]})
    await atom._on_decision({"symbol": SYM, "account_id": ACC, "cycle_id": BEFORE_CYCLE,
                             "direction": "buy", "strength": 0.75})
    await atom._on_verdict({"symbol": SYM, "cycle_id": BEFORE_CYCLE,
                            "metadata": {"approved": True}})
    before = bus.last("perpetual.target.state")
    bad += show("١· قبل القطع: قرار مستقرّ",
                bool(before) and before.get("direction") == "buy",
                "%s · %s" % (before.get("direction"), before.get("target_net")))

    emitted = bus.count("perpetual.target.state")
    for _ in range(5):                       # the silence: nothing arrives at all
        await asyncio.sleep(0)
    bad += show("٨· الصمت لا يُنتج قرارًا ولا موافقة",
                bus.count("perpetual.target.state") == emitted,
                "نشرات %d ⟶ %d" % (emitted, bus.count("perpetual.target.state")))

    await atom._on_decision({"symbol": SYM, "account_id": ACC, "cycle_id": AFTER_CYCLE,
                             "direction": "sell", "strength": 0.75})
    await atom._on_verdict({"symbol": SYM, "cycle_id": AFTER_CYCLE,
                            "metadata": {"approved": True}})
    after = bus.last("perpetual.target.state")
    bad += show("٥+٦· بعد العودة: يتبنّى الدورة الجديدة",
                after.get("direction") == "sell" and after.get("filter_verdict") == "FILTER_PASSED",
                "%s · %s" % (after.get("direction"), after.get("target_net")))

    await atom._on_verdict({"symbol": SYM, "cycle_id": BEFORE_CYCLE,
                            "metadata": {"approved": True}})
    await atom._on_decision({"symbol": SYM, "account_id": ACC, "cycle_id": BEFORE_CYCLE,
                             "direction": "buy", "strength": 0.95})
    revived = bus.last("perpetual.target.state")
    bad += show("٧· دورة ما قبل القطع لا تُبعث بعده",
                revived.get("direction") == "sell",
                "%s · %s" % (revived.get("direction"), revived.get("filter_verdict")))

    for name in FORBIDDEN:
        bad += show("٨· لا حدث %s" % name, bus.count(name) == 0, str(bus.count(name)))
    return bad


async def main_async() -> int:
    print("=" * 96)
    print("٤-٤ · انقطاع الناقل وإعادة الاتّصال — على الذرات الحقيقيّة")
    print("=" * 96)
    bad = await reference() + await account() + await decision()
    print("\n" + "=" * 96)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الصمت يُعلَن ولا يُقرأ صحّة · والعودة تُبنى من الجديد · ولا موافقة ضمنيّة.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
