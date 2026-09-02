"""Phase 4-6 — the stale account, against the REAL atoms.

Item 55 made the account state SPEAK its age instead of going silent: `age_s`,
`stale` and a declared `max_age_s` now travel on every publish. That closed the
silence. It did NOT, by itself, prove that anything downstream REFUSES to act on
a stale account -- and the owner's point 3 is exactly that:

    "a stale account must not produce an executable decision."

So this guard measures two different things and never conflates them:
  * the producer 619 -- fresh, stale, recovery, ordering, duplicates;
  * the CONSUMERS -- a full census of all 212 cards for who subscribes to
    `platform.account.state`, and whether any of them reads `stale`/`age_s` at
    all. A flag nobody consumes is a flag, not a guard.

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
ACCOUNT = "platform.account.state"
FORBIDDEN = ("execution.order.requested", "trading.final_decision", "brain_signal.written")
ACC, SYM = "52992818", "BTCUSD"
T0 = 1_000_000.0
MAX_AGE = 300.0


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


async def build(atom_id: str, bus: Bus, overrides=None):
    directory = ATOMS / FOLDERS[atom_id]
    spec = importlib.util.spec_from_file_location("_p46_%s" % atom_id, directory / "atom.py")
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


def write_row(db, stamp):
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY,"
                 " account_id TEXT, balance REAL, equity REAL, margin REAL,"
                 " free_margin REAL, margin_level REAL, currency TEXT, leverage INTEGER,"
                 " open_count INTEGER, broker TEXT, account_server TEXT, margin_mode TEXT,"
                 " trade_allowed INTEGER, connected INTEGER, updated_at REAL)")
    conn.execute("DELETE FROM account")
    conn.execute("INSERT INTO account VALUES (1,?,644.84,644.84,0.0,644.84,0.0,'USD',"
                 "5000,0,'B','S','HEDGING',1,1,?)", (ACC, stamp))
    conn.commit()
    conn.close()


def show(label, ok, detail=""):
    print("   %-46s %-34s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def producer() -> int:
    print("\nأ) المنتج 619 — الطزاجة والتقادم والعودة والترتيب")
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "acct.db")
        write_row(db, T0 - 10.0)
        bus = Bus()
        atom = await build("619", bus, {"db_path": db, "max_age_s": MAX_AGE})
        await atom.stop()

        async def pulse(now):
            for handler in bus.wired.get("SYS_SECOND", []):
                await handler({"official_time": now})

        await pulse(T0)
        await atom._read_once()
        fresh = bus.last(ACCOUNT)
        bad += show("١· حساب حديث", bool(fresh) and fresh.get("stale") is False,
                    "عمر=%s" % (fresh or {}).get("age_s"))

        bus.log.clear()
        await pulse(T0 + MAX_AGE * 10)
        await atom._read_once()
        stale = bus.last(ACCOUNT)
        bad += show("٢· تأخّر ⟶ متقادم ومعلَن",
                    bool(stale) and stale.get("stale") is True,
                    "عمر=%s" % (stale or {}).get("age_s"))

        write_row(db, T0 + MAX_AGE * 10 + 5.0)
        bus.log.clear()
        await pulse(T0 + MAX_AGE * 10 + 6.0)
        await atom._read_once()
        back = bus.last(ACCOUNT)
        bad += show("٤· بيانات جديدة تعيده حديثًا",
                    bool(back) and back.get("stale") is False,
                    "عمر=%s" % (back or {}).get("age_s"))

        write_row(db, T0 - 500.0)          # an OLD row lands after the update
        bus.log.clear()
        await pulse(T0 + MAX_AGE * 10 + 7.0)
        await atom._read_once()
        rolled = bus.last(ACCOUNT)
        bad += show("٥· صفّ قديم يُعلَن متقادمًا لا حديثًا",
                    rolled is None or rolled.get("stale") is True,
                    "متقادم=%s · عمر=%s" % ((rolled or {}).get("stale"),
                                             (rolled or {}).get("age_s")))

        bus.log.clear()
        for _ in range(3):                 # duplicates + ordering
            await pulse(T0 + MAX_AGE * 10 + 8.0)
            await atom._read_once()
        repeated = bus.last(ACCOUNT)
        bad += show("٦· التكرار لا يغيّر الحكم",
                    repeated is None or repeated.get("stale") is True,
                    "نشرات=%d" % bus.count(ACCOUNT))
    return bad


def consumers() -> int:
    print("\nب) المستهلكون — من يسمع الحساب، ومن يقرأ تقادمه فعلًا")
    subs, readers = [], []
    for folder in sorted(ATOMS.iterdir()):
        manifest = folder / "manifest.yaml"
        code = folder / "atom.py"
        if not manifest.exists() or not code.exists():
            continue
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if ACCOUNT not in (data.get("subscribes") or []):
            continue
        atom_id = str(data.get("id"))
        subs.append(atom_id)
        src = "\n".join(l for l in code.read_text(encoding="utf-8").splitlines()
                        if not l.lstrip().startswith("#"))
        if '"stale"' in src or "'stale'" in src or '"age_s"' in src or "'age_s'" in src:
            readers.append(atom_id)
    print("      يسمعون الحساب : %s" % " · ".join(subs))
    print("      يقرأون التقادم: %s" % (" · ".join(readers) if readers else "لا أحد"))
    return subs, readers


async def margin_guard() -> int:
    """The sharp end: 585 decides "is there enough money?" from `free_margin`.

    Measured before this barrier existed: a 61-hour-old account reached it with
    exactly the same confidence as a one-second-old one, because nothing read
    `stale`. The number it trusts may be dead. A stale account must not be able
    to certify margin -- and the refusal must be traceable by name, not a bare
    False.
    """
    print("\n٣· حارس الهامش 585 أمام حساب متقادم:")
    bad = 0
    bus = Bus()
    atom = await build("585", bus)
    await atom._on_specs({"symbols": [{"symbol": SYM, "margin_per_volume": 10.0,
                                       "tick_value": 1.0, "tick_size": 1.0,
                                       "contract_size": 1.0}]})
    order = {"request_id": "r-1", "account_id": ACC, "symbol": SYM, "action": "OPEN",
             "side": "BUY", "volume": 0.1, "reference_price": 63000.0}

    await atom._on_account({"account_id": ACC, "free_margin": 644.84, "equity": 644.84,
                            "leverage": 5000, "stale": False, "age_s": 1.0,
                            "max_age_s": MAX_AGE})
    await atom._on_order(dict(order))
    fresh = bus.last("risk.margin.validation.completed")
    bad += show("حساب حديث ⟶ يمرّ", bool(fresh) and fresh.get("approved") is True,
                "approved=%s · %s" % ((fresh or {}).get("approved"),
                                      (fresh or {}).get("reason") or "-"))

    bus.log.clear()
    await atom._on_account({"account_id": ACC, "free_margin": 644.84, "equity": 644.84,
                            "leverage": 5000, "stale": True, "age_s": 220291.0,
                            "max_age_s": MAX_AGE})
    await atom._on_order({**order, "request_id": "r-2"})
    stale = bus.last("risk.margin.validation.completed")
    bad += show("حساب متقادم ⟶ لا يُجيز الهامش",
                bool(stale) and stale.get("approved") is False,
                "approved=%s" % (stale or {}).get("approved"))
    bad += show("والسبب صريح قابل للتتبّع",
                bool(stale) and "STALE" in str(stale.get("reason") or "").upper(),
                str((stale or {}).get("reason")))
    # The age must be load-bearing, not merely carried: the refusal states the
    # number it refused on. Without this, dropping `age_s` changed nothing and
    # the break stayed green -- a barrier that measures nothing.
    bad += show("ويحمل العمر الذي رفض عليه",
                bool(stale) and stale.get("account_age_s") == 220291.0
                and stale.get("account_stale") is True,
                "عمر=%s · متقادم=%s" % ((stale or {}).get("account_age_s"),
                                         (stale or {}).get("account_stale")))
    import re as _re
    src585 = (ATOMS / FOLDERS["585"] / "atom.py").read_text(encoding="utf-8")
    found = _re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src585, _re.M)
    version = found.group(1) if found else ""
    bad += show("والنسخة تحرّكت وتطابق البطاقة",
                version not in ("", "1.1.0") and version == str(card("585").get("version")),
                "كود=%s · بطاقة=%s" % (version, card("585").get("version")))

    bus.log.clear()
    await atom._on_account({"account_id": ACC, "free_margin": 644.84, "equity": 644.84,
                            "leverage": 5000, "stale": False, "age_s": 2.0,
                            "max_age_s": MAX_AGE})
    await atom._on_order({**order, "request_id": "r-3"})
    back = bus.last("risk.margin.validation.completed")
    bad += show("وبعد العودة يمرّ ثانيةً", bool(back) and back.get("approved") is True,
                "approved=%s" % (back or {}).get("approved"))
    return bad


async def frozen_barrier() -> int:
    print("\n٧· التجميد حاجز مستقلّ عن الحساب:")
    bad = 0
    bus = Bus()
    engine = await build("581", bus)
    await engine._on_specs({"symbols": [{"symbol": SYM, "tick_value": 1.0, "tick_size": 1.0}]})
    await engine._on_tick({"symbol": SYM, "price": 63000.0})
    await engine._on_dial({"profiles": [{"account_id": ACC, "symbol": SYM,
                                         "stop_distance_frac": 0.0055}]})
    await engine._on_portfolio({"portfolios": [{"account_id": ACC, "symbol": SYM,
                                                "state": "FROZEN", "account_mode": "HEDGING"}]})
    await engine._on_positions({"source": "p46", "account_id": ACC, "positions": []})
    await engine._on_ledger({"ledgers": [{"account_id": ACC, "symbol": SYM,
                                          "risk_budget": 100.0, "v_net": 0.0}]})
    await engine._on_decision({"symbol": SYM, "account_id": ACC,
                               "cycle_id": "BTCUSD|60s|2000", "direction": "buy",
                               "strength": 0.75})
    await engine._on_verdict({"symbol": SYM, "cycle_id": "BTCUSD|60s|2000",
                              "metadata": {"approved": True}})
    state = bus.last("perpetual.target.state")
    bad += show("التجميد يمنع رغم كل شيء",
                bool(state) and state.get("action") == "BLOCKED",
                "%s · %s" % ((state or {}).get("action"), (state or {}).get("reason")))
    for name in FORBIDDEN:
        bad += show("٨· لا حدث %s" % name, bus.count(name) == 0, str(bus.count(name)))
    return bad


async def main_async() -> int:
    print("=" * 98)
    print("٤-٦ · الحساب المتقادم — على الذرات الحقيقيّة")
    print("=" * 98)
    bad = await producer()
    subs, readers = consumers()
    bad += show("٣· أحد المستهلكين يمتنع بسبب التقادم", bool(readers),
                "قرّاء التقادم = %d من %d" % (len(readers), len(subs)))
    bad += await margin_guard()
    bad += await frozen_barrier()
    print("\n" + "=" * 98)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: التقادم يُعلَن ويُقرَأ · والعودة من الجديد · والتجميد مستقلّ.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
